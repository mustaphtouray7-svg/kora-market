import secrets
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import click
from flask import Flask, flash, redirect, render_template, request, session, url_for
from werkzeug.exceptions import NotFound

from store.config import Config
from store.extensions import csrf, db, login_manager, migrate


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(Config)
    if test_config is not None:
        app.config.update(test_config)

    if not app.config.get("SECRET_KEY"):
        if app.config.get("APP_ENV") == "production":
            raise RuntimeError("Set SECRET_KEY before running in production.")
        app.config["SECRET_KEY"] = secrets.token_hex(32)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)

    from store import models  # noqa: F401
    from store.auth import auth

    app.register_blueprint(auth)

    @app.get("/")
    def index():
        products = (
            db.session.query(models.Product)
            .filter_by(is_active=True)
            .order_by(models.Product.created_at.desc())
            .all()
        )
        return render_template("index.html", products=products)

    @app.get("/products")
    def catalog():
        products = (
            db.session.query(models.Product)
            .filter_by(is_active=True)
            .order_by(models.Product.created_at.desc())
            .all()
        )
        return render_template("catalog.html", products=products)

    @app.get("/products/<int:product_id>")
    def product_detail(product_id):
        product = (
            db.session.query(models.Product)
            .filter_by(id=product_id, is_active=True)
            .first_or_404()
        )
        return render_template("product_detail.html", product=product)

    def get_cart():
        cart = session.get("cart", {})
        return {int(product_id): max(1, int(quantity)) for product_id, quantity in cart.items() if int(product_id) > 0 and int(quantity) > 0}

    def get_cart_items():
        cart = get_cart()
        if not cart:
            return []
        products = models.Product.query.filter(models.Product.id.in_(cart.keys()), models.Product.is_active.is_(True)).all()
        items = []
        for product in products:
            quantity = cart.get(product.id, 0)
            items.append({
                "product": product,
                "quantity": quantity,
                "line_total": product.price * quantity,
            })
        return items

    @app.get("/cart")
    def cart():
        items = get_cart_items()
        total = sum((item["line_total"] for item in items), Decimal("0.00"))
        return render_template("cart.html", items=items, total=total)

    @app.post("/cart/add/<int:product_id>")
    def add_to_cart(product_id):
        product = models.Product.query.filter_by(id=product_id, is_active=True).first_or_404()
        cart = get_cart()
        cart[product_id] = cart.get(product_id, 0) + 1
        session["cart"] = {str(product_id): quantity for product_id, quantity in cart.items()}
        flash(f"{product.name} added to your cart.", "success")
        return redirect(url_for("catalog"))

    @app.post("/cart/update/<int:product_id>")
    def update_cart(product_id):
        quantity = max(0, int(request.form.get("quantity", 0) or 0))
        cart = get_cart()
        if quantity <= 0:
            cart.pop(product_id, None)
        else:
            cart[product_id] = quantity
        session["cart"] = {str(product_id): qty for product_id, qty in cart.items()}
        return redirect(url_for("cart"))

    @app.post("/cart/remove/<int:product_id>")
    def remove_from_cart(product_id):
        cart = get_cart()
        cart.pop(product_id, None)
        session["cart"] = {str(product_id): qty for product_id, qty in cart.items()}
        return redirect(url_for("cart"))

    @app.get("/checkout")
    def checkout():
        items = get_cart_items()
        if not items:
            flash("Your cart is empty.", "info")
            return redirect(url_for("catalog"))
        branches = models.Branch.query.filter_by(is_active=True).order_by(models.Branch.name.asc()).all()
        total = sum((item["line_total"] for item in items), Decimal("0.00"))
        return render_template("checkout.html", items=items, total=total, branches=branches)

    @app.post("/checkout")
    def submit_checkout():
        items = get_cart_items()
        if not items:
            flash("Your cart is empty.", "info")
            return redirect(url_for("catalog"))

        full_name = (request.form.get("full_name") or "").strip()
        phone_number = (request.form.get("phone_number") or "").strip()
        branch_id = request.form.get("branch_id") or None

        if not full_name or not phone_number:
            flash("Please provide your full name and phone number.", "error")
            return redirect(url_for("checkout"))

        branch = None
        if branch_id:
            branch = models.Branch.query.filter_by(id=branch_id, is_active=True).first()

        customer = models.Customer(full_name=full_name, phone_number=phone_number)
        db.session.add(customer)
        db.session.flush()

        order_number = f"KORA-{datetime.utcnow().strftime('%Y%m%d')}-{(db.session.query(models.Order.id).count() + 1):04d}"
        order = models.Order(
            order_number=order_number,
            customer=customer,
            branch=branch,
            status="pending",
            payment_status="pending",
            currency="GMD",
            total=Decimal("0.00"),
        )
        db.session.add(order)
        db.session.flush()

        total = Decimal("0.00")
        for item in items:
            product = item["product"]
            quantity = item["quantity"]
            subtotal = product.price * quantity
            total += subtotal
            order_item = models.OrderItem(
                order=order,
                product=product,
                product_name=product.name,
                quantity=quantity,
                unit_price=product.price,
                subtotal=subtotal,
            )
            db.session.add(order_item)
        order.total = total
        db.session.commit()
        session["cart"] = {}
        flash("Your order has been placed. Payment is still pending.", "success")
        return render_template("checkout_success.html", order=order)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.cli.command("create-admin")
    def create_admin():
        """Create an administrator account with a hashed password."""
        from store.models import AdminUser

        username = click.prompt("Admin username").strip().lower()
        if not username:
            raise click.ClickException("Username cannot be empty.")
        if AdminUser.query.filter_by(username=username).first():
            raise click.ClickException("An admin with that username already exists.")

        password = click.prompt(
            "Admin password (at least 12 characters)",
            hide_input=True,
            confirmation_prompt=True,
        )
        if len(password) < 12:
            raise click.ClickException("Password must be at least 12 characters.")

        admin = AdminUser(username=username)
        admin.set_password(password)
        db.session.add(admin)
        db.session.commit()
        click.echo(f"Created admin account '{username}'.")

    return app
