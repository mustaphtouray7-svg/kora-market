import re
from datetime import datetime, timezone
from functools import wraps

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from flask_wtf import FlaskForm
from sqlalchemy.exc import IntegrityError
from wtforms import PasswordField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Email, EqualTo, Length

from store.extensions import db
from store.marketplace_models import Seller, Shop

marketplace = Blueprint("marketplace", __name__)


def _slugify(value):
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return value[:140].strip("-")


def _admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated or not getattr(current_user, "is_admin", False):
            abort(403)
        return view(*args, **kwargs)
    return wrapped


class SellerRegistrationForm(FlaskForm):
    full_name = StringField("Your full name", validators=[DataRequired(), Length(min=2, max=160)])
    email = StringField("Email address", validators=[DataRequired(), Email(), Length(max=254)])
    phone_number = StringField("Phone number", validators=[DataRequired(), Length(min=6, max=32)])
    shop_name = StringField("Brand name", validators=[DataRequired(), Length(min=2, max=120)])
    category = SelectField(
        "Brand category",
        choices=[
            ("Fashion", "Fashion and clothing"),
            ("Electronics", "Electronics"),
            ("Groceries", "Groceries and food"),
            ("Home", "Home and furniture"),
            ("Beauty", "Beauty and personal care"),
            ("Other", "Other"),
        ],
        validators=[DataRequired()],
    )
    description = TextAreaField("Tell customers about your brand", validators=[DataRequired(), Length(min=10, max=1200)])
    password = PasswordField("Password (at least 8 characters)", validators=[DataRequired(), Length(min=8, max=128)])
    confirm_password = PasswordField("Confirm password", validators=[DataRequired(), EqualTo("password", message="Passwords must match.")])
    submit = SubmitField("Create a brand profile")


class SellerLoginForm(FlaskForm):
    email = StringField("Email address", validators=[DataRequired(), Email(), Length(max=254)])
    password = PasswordField("Password", validators=[DataRequired()])
    submit = SubmitField("Seller sign in")


@marketplace.get("/shops")
def shop_directory():
    shops = Shop.query.filter_by(status="approved").order_by(Shop.name.asc()).all()
    return render_template("marketplace/shops.html", shops=shops)


@marketplace.get("/shops/<slug>")
def shop_detail(slug):
    shop = Shop.query.filter_by(slug=slug, status="approved").first_or_404()
    return render_template("marketplace/shop_detail.html", shop=shop)


@marketplace.route("/seller/register", methods=["GET", "POST"])
def seller_register():
    if current_user.is_authenticated and getattr(current_user, "is_seller", False):
        return redirect(url_for("marketplace.seller_dashboard"))

    form = SellerRegistrationForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        if Seller.query.filter_by(email=email).first():
            flash("An account with that email already exists. Please sign in.", "error")
            return render_template("marketplace/seller_register.html", form=form)

        base_slug = _slugify(form.shop_name.data)
        if not base_slug:
            flash("Please choose a brand name containing letters or numbers.", "error")
            return render_template("marketplace/seller_register.html", form=form)
        slug = base_slug
        suffix = 2
        while Shop.query.filter_by(slug=slug).first():
            slug = f"{base_slug[:130]}-{suffix}"
            suffix += 1

        seller = Seller(
            full_name=form.full_name.data.strip(),
            email=email,
            phone_number=form.phone_number.data.strip(),
        )
        seller.set_password(form.password.data)
        shop = Shop(
            owner=seller,
            name=form.shop_name.data.strip(),
            slug=slug,
            category=form.category.data,
            description=form.description.data.strip(),
            contact_phone=form.phone_number.data.strip(),
            status="pending",
        )
        db.session.add_all([seller, shop])
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("We could not submit your application. Check your details and try again.", "error")
            return render_template("marketplace/seller_register.html", form=form)

        login_user(seller)
        flash("Your brand application was submitted. It will appear publicly after administrator approval.", "success")
        return redirect(url_for("marketplace.seller_dashboard"))

    return render_template("marketplace/seller_register.html", form=form)


@marketplace.route("/seller/sign-in", methods=["GET", "POST"])
def seller_login():
    if current_user.is_authenticated and getattr(current_user, "is_seller", False):
        return redirect(url_for("marketplace.seller_dashboard"))

    form = SellerLoginForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        seller = Seller.query.filter_by(email=email, is_active=True).first()
        if seller and seller.check_password(form.password.data):
            login_user(seller)
            return redirect(url_for("marketplace.seller_dashboard"))
        flash("The email address or password is incorrect.", "error")
    return render_template("marketplace/seller_login.html", form=form)


@marketplace.get("/seller/dashboard")
@login_required
def seller_dashboard():
    if not getattr(current_user, "is_seller", False):
        abort(403)
    shops = Shop.query.filter_by(owner_id=current_user.id).order_by(Shop.created_at.desc()).all()
    return render_template("marketplace/seller_dashboard.html", shops=shops)


@marketplace.post("/seller/sign-out")
@login_required
def seller_logout():
    if not getattr(current_user, "is_seller", False):
        abort(403)
    logout_user()
    flash("You have signed out of your seller account.", "success")
    return redirect(url_for("marketplace.seller_login"))


@marketplace.get("/admin/shops")
@login_required
@_admin_required
def admin_shops():
    shops = Shop.query.order_by(Shop.created_at.desc()).all()
    return render_template("marketplace/admin_shops.html", shops=shops)


@marketplace.post("/admin/shops/<int:shop_id>/review")
@login_required
@_admin_required
def review_shop(shop_id):
    shop = Shop.query.get_or_404(shop_id)
    decision = (request.form.get("decision") or "").strip().lower()
    note = (request.form.get("admin_note") or "").strip()[:1000]
    if decision not in {"approve", "reject", "suspend"}:
        abort(400)

    shop.status = {"approve": "approved", "reject": "rejected", "suspend": "suspended"}[decision]
    shop.admin_note = note or None
    shop.reviewed_at = datetime.now(timezone.utc)
    db.session.commit()
    flash(f"Brand '{shop.name}' status updated to {shop.status}.", "success")
    return redirect(url_for("marketplace.admin_shops"))
