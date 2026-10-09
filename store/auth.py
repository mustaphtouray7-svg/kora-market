from decimal import Decimal

from flask import Blueprint, flash, redirect, render_template, url_for
from flask_login import current_user, login_required, login_user, logout_user
from flask_wtf import FlaskForm
from sqlalchemy.exc import IntegrityError
from wtforms import BooleanField, DecimalField, IntegerField, PasswordField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Email, Length, NumberRange, Optional

from store.extensions import db
from store.models import AdminUser, Branch, Customer, Order, OrderItem, Product

auth = Blueprint("auth", __name__)


class LoginForm(FlaskForm):
    username = StringField(
        "Gmail address",
        validators=[DataRequired(), Email(), Length(max=80)],
    )
    password = PasswordField("Password", validators=[DataRequired()])
    submit = SubmitField("Sign in")


class BranchForm(FlaskForm):
    name = StringField("Branch name", validators=[DataRequired(), Length(max=120)])
    address = StringField("Address", validators=[Optional(), Length(max=300)])
    is_active = BooleanField("Active")
    submit = SubmitField("Save branch")


class ProductForm(FlaskForm):
    name = StringField("Product name", validators=[DataRequired(), Length(max=160)])
    description = TextAreaField("Description", validators=[Optional(), Length(max=2000)])
    price = DecimalField("Price", places=2, validators=[DataRequired()])
    image_url = StringField("Image URL", validators=[Optional(), Length(max=500)])
    remove_image = BooleanField("Remove current image")
    stock_quantity = IntegerField(
        "Stock quantity",
        validators=[DataRequired(), NumberRange(min=0)],
    )
    is_active = BooleanField("Active")
    submit = SubmitField("Save product")


@auth.route("/admin/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("auth.dashboard"))

    form = LoginForm()
    if form.validate_on_submit():
        username = form.username.data.strip().lower()
        admin = AdminUser.query.filter_by(username=username).first()
        if admin is not None and admin.is_active and admin.check_password(form.password.data):
            login_user(admin)
            flash("You are signed in.", "success")
            return redirect(url_for("auth.dashboard"))
        flash("The Gmail address or password is incorrect.", "error")

    return render_template("auth/login.html", form=form)


@auth.get("/admin")
@login_required
def dashboard():
    product_count = Product.query.count()
    branch_count = Branch.query.count()
    order_count = Order.query.count()
    customer_count = Customer.query.count()
    pending_order_count = Order.query.filter_by(payment_status="pending").count()
    return render_template(
        "auth/dashboard.html",
        product_count=product_count,
        branch_count=branch_count,
        order_count=order_count,
        customer_count=customer_count,
        pending_order_count=pending_order_count,
    )

@auth.get("/admin/orders")
@login_required
def orders():
    order_list = Order.query.order_by(Order.created_at.desc()).all()
    return render_template("auth/orders.html", orders=order_list)

@auth.get("/admin/orders/<int:order_id>")
@login_required
def order_detail(order_id):
    order = Order.query.get_or_404(order_id)
    return render_template("auth/order_detail.html", order=order)

@auth.get("/admin/customers")
@login_required
def customers():
    customer_list = Customer.query.order_by(Customer.created_at.desc()).all()
    return render_template("auth/customers.html", customers=customer_list)


@auth.get("/admin/products")
@login_required
def products():
    product_list = Product.query.order_by(Product.created_at.desc()).all()
    return render_template("auth/products.html", products=product_list)


@auth.get("/admin/products/new")
@login_required
def new_product():
    form = ProductForm()
    return render_template("auth/product_form.html", form=form, product=None)


@auth.post("/admin/products/new")
@login_required
def create_product():
    form = ProductForm()
    if form.validate_on_submit():
        product = Product(
            name=form.name.data.strip(),
            description=(form.description.data or "").strip(),
            price=Decimal(str(form.price.data)),
            image_url=None if form.remove_image.data else ((form.image_url.data or "").strip() or None),
            stock_quantity=form.stock_quantity.data,
            is_active=form.is_active.data,
        )
        try:
            db.session.add(product)
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("A product with that name already exists.", "error")
            return render_template("auth/product_form.html", form=form, product=None)
        flash("Product created successfully.", "success")
        return redirect(url_for("auth.products"))
    return render_template("auth/product_form.html", form=form, product=None)


@auth.get("/admin/products/<int:product_id>/edit")
@login_required
def edit_product(product_id):
    product = Product.query.get_or_404(product_id)
    form = ProductForm(obj=product)
    return render_template("auth/product_form.html", form=form, product=product)


@auth.post("/admin/products/<int:product_id>/edit")
@login_required
def update_product(product_id):
    product = Product.query.get_or_404(product_id)
    form = ProductForm()
    if form.validate_on_submit():
        product.name = form.name.data.strip()
        product.description = (form.description.data or "").strip()
        product.price = Decimal(str(form.price.data))
        product.image_url = None if form.remove_image.data else ((form.image_url.data or "").strip() or None)
        product.stock_quantity = form.stock_quantity.data
        product.is_active = form.is_active.data
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("A product with that name already exists.", "error")
            return render_template("auth/product_form.html", form=form, product=product)
        flash("Product updated successfully.", "success")
        return redirect(url_for("auth.products"))
    return render_template("auth/product_form.html", form=form, product=product)


@auth.post("/admin/products/<int:product_id>/toggle")
@login_required
def toggle_product(product_id):
    product = Product.query.get_or_404(product_id)
    product.is_active = not product.is_active
    db.session.commit()
    flash(f"Product '{product.name}' is now {'active' if product.is_active else 'inactive'}.", "success")
    return redirect(url_for("auth.products"))


@auth.post("/admin/products/<int:product_id>/delete")
@login_required
def delete_product(product_id):
    product = Product.query.get_or_404(product_id)
    # Keep historical order lines intact while removing the catalog product.
    OrderItem.query.filter_by(product_id=product.id).update(
        {OrderItem.product_id: None}, synchronize_session=False
    )
    db.session.delete(product)
    db.session.commit()
    flash(f"Product '{product.name}' was deleted.", "success")
    return redirect(url_for("auth.products"))


@auth.get("/admin/branches")
@login_required
def branches():
    branch_list = Branch.query.order_by(Branch.name.asc()).all()
    return render_template("auth/branches.html", branches=branch_list)


@auth.get("/admin/branches/new")
@login_required
def new_branch():
    form = BranchForm()
    return render_template("auth/branch_form.html", form=form, branch=None)


@auth.post("/admin/branches/new")
@login_required
def create_branch():
    form = BranchForm()
    if form.validate_on_submit():
        branch = Branch(
            name=form.name.data.strip(),
            address=(form.address.data or "").strip() or None,
            is_active=form.is_active.data,
        )
        try:
            db.session.add(branch)
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("A branch with that name already exists.", "error")
            return render_template("auth/branch_form.html", form=form, branch=None)
        flash("Branch created successfully.", "success")
        return redirect(url_for("auth.branches"))
    return render_template("auth/branch_form.html", form=form, branch=None)


@auth.get("/admin/branches/<int:branch_id>/edit")
@login_required
def edit_branch(branch_id):
    branch = Branch.query.get_or_404(branch_id)
    form = BranchForm(obj=branch)
    return render_template("auth/branch_form.html", form=form, branch=branch)


@auth.post("/admin/branches/<int:branch_id>/edit")
@login_required
def update_branch(branch_id):
    branch = Branch.query.get_or_404(branch_id)
    form = BranchForm()
    if form.validate_on_submit():
        branch.name = form.name.data.strip()
        branch.address = (form.address.data or "").strip() or None
        branch.is_active = form.is_active.data
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("A branch with that name already exists.", "error")
            return render_template("auth/branch_form.html", form=form, branch=branch)
        flash("Branch updated successfully.", "success")
        return redirect(url_for("auth.branches"))


@auth.post("/admin/branches/<int:branch_id>/toggle")
@login_required
def toggle_branch(branch_id):
    branch = Branch.query.get_or_404(branch_id)
    branch.is_active = not branch.is_active
    db.session.commit()
    flash(f"Branch '{branch.name}' is now {'active' if branch.is_active else 'inactive'}.", "success")
    return redirect(url_for("auth.branches"))


@auth.post("/admin/logout")
@login_required
def logout():
    logout_user()
    flash("You have signed out.", "success")
    return redirect(url_for("auth.login"))
