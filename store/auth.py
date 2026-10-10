from decimal import Decimal
from functools import wraps

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from flask_wtf import FlaskForm
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from wtforms import BooleanField, DecimalField, IntegerField, PasswordField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Email, EqualTo, Length, NumberRange, Optional

from store.extensions import db
from store.models import AdminUser, StaffUser, Branch, Customer, Order, OrderItem, Product

auth = Blueprint("auth", __name__)


def admin_required(view):
    """Allow write actions only for administrator accounts."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated or not getattr(current_user, "is_admin", False):
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def _resolve_product_image(form, current_url=None):
    """Use an image URL only and preserve existing images when no new URL is entered."""
    if form.remove_image.data:
        return None
    image_url = (form.image_url.data or "").strip()
    if image_url:
        return image_url
    return current_url


class LoginForm(FlaskForm):
    username = StringField(
        "Gmail address",
        validators=[DataRequired(), Email(), Length(max=80)],
    )
    password = PasswordField("Password", validators=[DataRequired()])
    submit = SubmitField("Sign in")


class CustomerRegistrationForm(FlaskForm):
    full_name = StringField("Full name", validators=[DataRequired(), Length(min=2, max=160)])
    phone_number = StringField("Phone number", validators=[DataRequired(), Length(min=6, max=32)])
    address = StringField("Home address", validators=[DataRequired(), Length(min=5, max=300)])
    password = PasswordField("Password (at least 8 characters)", validators=[DataRequired(), Length(min=8, max=128)])
    confirm_password = PasswordField("Confirm password", validators=[DataRequired(), EqualTo("password", message="Passwords must match.")])
    submit = SubmitField("Create customer account")


class CustomerLoginForm(FlaskForm):
    phone_number = StringField("Phone number", validators=[DataRequired(), Length(min=6, max=32)])
    password = PasswordField("Password", validators=[DataRequired()])
    submit = SubmitField("Customer sign in")


class StaffLoginForm(FlaskForm):
    email = StringField("Gmail address", validators=[DataRequired(), Email(), Length(max=254)])
    password = PasswordField("Password", validators=[DataRequired()])
    submit = SubmitField("Sign in as staff")


class StaffAccountForm(FlaskForm):
    email = StringField("Staff Gmail address", validators=[DataRequired(), Email(), Length(max=254)])
    password = PasswordField("Temporary password (at least 12 characters)", validators=[DataRequired(), Length(min=12, max=128)])
    confirm_password = PasswordField("Confirm password", validators=[DataRequired(), EqualTo("password", message="Passwords must match.")])
    submit = SubmitField("Create staff account")


class BranchForm(FlaskForm):
    name = StringField("Branch name", validators=[DataRequired(), Length(max=120)])
    address = StringField("Address", validators=[Optional(), Length(max=300)])
    is_active = BooleanField("Active")
    submit = SubmitField("Save branch")


class ProductForm(FlaskForm):
    name = StringField("Product name", validators=[DataRequired(), Length(max=160)])
    description = TextAreaField("Description", validators=[Optional(), Length(max=2000)])
    price = DecimalField("Price", places=2, validators=[DataRequired()])
    image_url = StringField("Image URL (optional)", validators=[Optional(), Length(max=500)])
    remove_image = BooleanField("Remove current image")
    stock_quantity = IntegerField(
        "Stock quantity",
        validators=[DataRequired(), NumberRange(min=0)],
    )
    is_active = BooleanField("Active")
    submit = SubmitField("Save product")


@auth.route("/admin/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated and getattr(current_user, "is_admin", False):
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


@auth.route("/staff/login", methods=["GET", "POST"])
def staff_login():
    # Allow customers and administrators to switch to a staff account.
    if current_user.is_authenticated and not getattr(current_user, "is_admin", False) and not getattr(current_user, "is_customer", False):
        return redirect(url_for("auth.dashboard"))
    form = StaffLoginForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        staff = StaffUser.query.filter_by(email=email).first()
        if staff is not None and staff.is_active and staff.check_password(form.password.data):
            login_user(staff)
            flash("You are signed in as staff.", "success")
            return redirect(url_for("auth.dashboard"))
        flash("The Gmail address or password is incorrect.", "error")
    return render_template("auth/staff_login.html", form=form)




@auth.route("/customer/register", methods=["GET", "POST"])
def customer_register():
    if current_user.is_authenticated and getattr(current_user, "is_customer", False):
        return redirect(url_for("auth.customer_account"))
    form = CustomerRegistrationForm()
    if form.validate_on_submit():
        phone_number = form.phone_number.data.strip()
        existing_account = Customer.query.filter(
            Customer.phone_number == phone_number,
            Customer.password_hash.isnot(None),
        ).first()
        if existing_account:
            flash("An account with that phone number already exists. Please sign in instead.", "error")
            return render_template("auth/customer_register.html", form=form)
        customer = Customer(
            full_name=form.full_name.data.strip(),
            phone_number=phone_number,
            address=form.address.data.strip(),
        )
        customer.set_password(form.password.data)
        db.session.add(customer)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("We could not create the account with those details. Please check and try again.", "error")
            return render_template("auth/customer_register.html", form=form)
        login_user(customer)
        flash("Your customer account has been created.", "success")
        return redirect(url_for("auth.customer_account"))
    return render_template("auth/customer_register.html", form=form)


@auth.route("/customer/sign-in", methods=["GET", "POST"])
def customer_login():
    if current_user.is_authenticated and getattr(current_user, "is_customer", False):
        return redirect(url_for("auth.customer_account"))
    form = CustomerLoginForm()
    if form.validate_on_submit():
        phone_number = form.phone_number.data.strip()
        registered_customers = Customer.query.filter(
            Customer.phone_number == phone_number,
            Customer.password_hash.isnot(None),
        ).all()
        customer = next((item for item in registered_customers if item.check_password(form.password.data)), None)
        if customer is not None:
            login_user(customer)
            flash("You are signed in to your customer account.", "success")
            return redirect(url_for("auth.customer_account"))
        flash("The phone number or password is incorrect.", "error")
    return render_template("auth/customer_login.html", form=form)


@auth.get("/customer/account")
@login_required
def customer_account():
    if not getattr(current_user, "is_customer", False):
        return redirect(url_for("auth.dashboard"))
    customer_orders = Order.query.filter_by(customer_id=current_user.id).order_by(Order.created_at.desc()).all()
    return render_template("auth/customer_account.html", customer=current_user, orders=customer_orders)


@auth.get("/admin/staff")
@login_required
@admin_required
def staff_accounts():
    staff_list = StaffUser.query.order_by(StaffUser.created_at.desc()).all()
    return render_template("auth/staff_accounts.html", staff_list=staff_list)


@auth.route("/admin/staff/new", methods=["GET", "POST"])
@login_required
@admin_required
def new_staff_account():
    form = StaffAccountForm(email="mustaphamarrenah@gmail.com")
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        if StaffUser.query.filter_by(email=email).first():
            flash("A staff account with that email already exists.", "error")
            return render_template("auth/staff_form.html", form=form)
        staff = StaffUser(email=email)
        staff.set_password(form.password.data)
        db.session.add(staff)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("A staff account with that email already exists.", "error")
            return render_template("auth/staff_form.html", form=form)
        flash("Staff account created. Give the staff member their temporary password securely.", "success")
        return redirect(url_for("auth.staff_accounts"))
    return render_template("auth/staff_form.html", form=form)


@auth.post("/admin/staff/<int:staff_id>/toggle")
@login_required
@admin_required
def toggle_staff_account(staff_id):
    staff = StaffUser.query.get_or_404(staff_id)
    staff.is_active = not staff.is_active
    db.session.commit()
    flash(f"Staff account {staff.email} is now {'active' if staff.is_active else 'disabled'}.", "success")
    return redirect(url_for("auth.staff_accounts"))


@auth.get("/admin")
@login_required
def dashboard():
    if getattr(current_user, "is_customer", False):
        return redirect(url_for("auth.customer_account"))
    product_count = Product.query.count()
    branch_count = Branch.query.count()
    order_count = Order.query.count()
    customer_count = Customer.query.count()
    pending_order_count = Order.query.filter_by(payment_status="pending").count()
    if not getattr(current_user, "is_admin", False):
        recent_orders = (
            Order.query.join(Customer)
            .order_by(Order.created_at.desc())
            .limit(15)
            .all()
        )
        return render_template(
            "auth/staff_dashboard.html",
            order_count=order_count,
            customer_count=customer_count,
            pending_order_count=pending_order_count,
            recent_orders=recent_orders,
        )
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
    if getattr(current_user, "is_customer", False):
        return redirect(url_for("auth.customer_account"))
    search = (request.args.get("q") or "").strip()
    query = Order.query.join(Customer)
    if search:
        pattern = f"%{search}%"
        query = query.filter(or_(
            Order.order_number.ilike(pattern),
            Customer.full_name.ilike(pattern),
            Customer.phone_number.ilike(pattern),
        ))
    order_list = query.order_by(Order.created_at.desc()).all()
    return render_template("auth/orders.html", orders=order_list, search=search)

@auth.get("/admin/orders/<int:order_id>")
@login_required
def order_detail(order_id):
    order = Order.query.get_or_404(order_id)
    if getattr(current_user, "is_customer", False) and order.customer_id != current_user.id:
        abort(403)
    return render_template("auth/order_detail.html", order=order)

@auth.post("/admin/orders/<int:order_id>/collection-status")
@login_required
def update_collection_status(order_id):
    if getattr(current_user, "is_customer", False):
        abort(403)
    order = Order.query.get_or_404(order_id)
    new_status = (request.form.get("collection_status") or "").strip().lower()
    if new_status not in {"collected", "not_collected"}:
        abort(400)
    if new_status == "collected" and order.payment_status != "paid":
        flash("This order cannot be marked collected until its payment is confirmed.", "error")
        return redirect(url_for("auth.order_detail", order_id=order.id))
    order.collection_status = new_status
    db.session.commit()
    flash(
        "Order marked as collected." if new_status == "collected" else "Order marked as not collected.",
        "success",
    )
    return redirect(url_for("auth.order_detail", order_id=order.id))


@auth.get("/admin/customers")
@login_required
def customers():
    if getattr(current_user, "is_customer", False):
        return redirect(url_for("auth.customer_account"))
    search = (request.args.get("q") or "").strip()
    query = Customer.query
    if search:
        pattern = f"%{search}%"
        query = query.filter(or_(
            Customer.full_name.ilike(pattern),
            Customer.phone_number.ilike(pattern),
        ))
    customer_list = query.order_by(Customer.created_at.desc()).all()
    return render_template("auth/customers.html", customers=customer_list, search=search)


@auth.get("/admin/products")
@login_required
@admin_required
def products():
    product_list = Product.query.order_by(Product.created_at.desc()).all()
    return render_template("auth/products.html", products=product_list)


@auth.get("/admin/products/new")
@login_required
@admin_required
def new_product():
    form = ProductForm()
    return render_template("auth/product_form.html", form=form, product=None)


@auth.post("/admin/products/new")
@login_required
@admin_required
def create_product():
    form = ProductForm()
    if form.validate_on_submit():
        try:
            image_url = _resolve_product_image(form)
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template("auth/product_form.html", form=form, product=None)
        product = Product(
            name=form.name.data.strip(),
            description=(form.description.data or "").strip(),
            price=Decimal(str(form.price.data)),
            image_url=image_url,
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
@admin_required
def edit_product(product_id):
    product = Product.query.get_or_404(product_id)
    form = ProductForm(obj=product)
    return render_template("auth/product_form.html", form=form, product=product)


@auth.post("/admin/products/<int:product_id>/edit")
@login_required
@admin_required
def update_product(product_id):
    product = Product.query.get_or_404(product_id)
    form = ProductForm()
    if form.validate_on_submit():
        product.name = form.name.data.strip()
        product.description = (form.description.data or "").strip()
        product.price = Decimal(str(form.price.data))
        try:
            product.image_url = _resolve_product_image(form, current_url=product.image_url)
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template("auth/product_form.html", form=form, product=product)
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
@admin_required
def toggle_product(product_id):
    product = Product.query.get_or_404(product_id)
    product.is_active = not product.is_active
    db.session.commit()
    flash(f"Product '{product.name}' is now {'active' if product.is_active else 'inactive'}.", "success")
    return redirect(url_for("auth.products"))


@auth.post("/admin/products/<int:product_id>/delete")
@login_required
@admin_required
def delete_product(product_id):
    product = Product.query.get_or_404(product_id)
    product_name = product.name
    # Keep historical order lines intact while removing the catalog product.
    OrderItem.query.filter_by(product_id=product.id).update(
        {OrderItem.product_id: None}, synchronize_session=False
    )
    db.session.delete(product)
    db.session.commit()
    flash(f"Product '{product_name}' was deleted.", "success")
    return redirect(url_for("auth.products"))


@auth.get("/admin/branches")
@login_required
@admin_required
def branches():
    branch_list = Branch.query.order_by(Branch.name.asc()).all()
    return render_template("auth/branches.html", branches=branch_list)


@auth.get("/admin/branches/new")
@login_required
@admin_required
def new_branch():
    form = BranchForm()
    return render_template("auth/branch_form.html", form=form, branch=None)


@auth.post("/admin/branches/new")
@login_required
@admin_required
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
@admin_required
def edit_branch(branch_id):
    branch = Branch.query.get_or_404(branch_id)
    form = BranchForm(obj=branch)
    return render_template("auth/branch_form.html", form=form, branch=branch)


@auth.post("/admin/branches/<int:branch_id>/edit")
@login_required
@admin_required
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
@admin_required
def toggle_branch(branch_id):
    branch = Branch.query.get_or_404(branch_id)
    branch.is_active = not branch.is_active
    db.session.commit()
    flash(f"Branch '{branch.name}' is now {'active' if branch.is_active else 'inactive'}.", "success")
    return redirect(url_for("auth.branches"))


@auth.post("/admin/logout")
@login_required
def logout():
    was_admin = getattr(current_user, "is_admin", False)
    was_customer = getattr(current_user, "is_customer", False)
    logout_user()
    flash("You have signed out.", "success")
    endpoint = "auth.login" if was_admin else ("auth.customer_login" if was_customer else "auth.staff_login")
    return redirect(url_for(endpoint))
