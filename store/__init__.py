import hashlib
import hmac
import json
import secrets
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import click
from flask import Flask, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user
from sqlalchemy import inspect, or_, text
from werkzeug.exceptions import NotFound
from werkzeug.middleware.proxy_fix import ProxyFix

from store.config import Config
from store.extensions import csrf, db, login_manager, migrate


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
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

    @app.before_request
    def enforce_https_in_production():
        if app.config.get("APP_ENV") == "production" and not request.is_secure:
            return redirect(request.url.replace("http://", "https://", 1), code=308)

    from store import models  # noqa: F401
    from store.marketplace_models import Seller, Shop, ShopProduct
    from store.auth import auth
    from store.marketplace import marketplace as marketplace_blueprint

    app.register_blueprint(auth)
    app.register_blueprint(marketplace_blueprint)

    # Keep the existing customer/admin/staff login IDs working while adding sellers.
    @login_manager.user_loader
    def load_user(user_id):
        if user_id.startswith("seller:"):
            seller_id = user_id.split(":", 1)[1]
            if not seller_id.isdecimal():
                return None
            seller = db.session.get(Seller, int(seller_id))
            return seller if seller is not None and seller.is_active else None
        return models.load_admin_user(user_id)

    @app.context_processor
    def inject_marketplace_shops():
        return {
            "approved_shops": Shop.query.filter_by(status="approved")
            .order_by(Shop.name.asc()).limit(6).all()
        }

    # Create the staff table and safely add the pickup-status column to existing orders.
    # Inspection keeps this migration compatible with both PostgreSQL and SQLite.
    with app.app_context():
        db.metadata.create_all(bind=db.engine, tables=[models.StaffUser.__table__, Seller.__table__, Shop.__table__, ShopProduct.__table__])
        inspector = inspect(db.engine)
        if inspector.has_table("shops"):
            shop_columns = {column["name"] for column in inspector.get_columns("shops")}
            if "image_data" not in shop_columns:
                with db.engine.begin() as connection:
                    connection.execute(text("ALTER TABLE shops ADD COLUMN image_data TEXT"))
        # Add nullable customer-account fields to existing customer records.
        # Existing guest checkouts remain valid and can continue without an account.
        if inspector.has_table("customers"):
            customer_columns = {column["name"] for column in inspector.get_columns("customers")}
            with db.engine.begin() as connection:
                if "email" not in customer_columns:
                    connection.execute(text("ALTER TABLE customers ADD COLUMN email VARCHAR(254)"))
                if "password_hash" not in customer_columns:
                    connection.execute(text("ALTER TABLE customers ADD COLUMN password_hash VARCHAR(256)"))
                if "address" not in customer_columns:
                    connection.execute(text("ALTER TABLE customers ADD COLUMN address VARCHAR(300)"))
                connection.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ix_customers_email ON customers (email)"
                ))

        # Add product categories to existing databases without disturbing current products.
        if inspector.has_table("products"):
            product_columns = {column["name"] for column in inspector.get_columns("products")}
            if "category" not in product_columns:
                with db.engine.begin() as connection:
                    connection.execute(text("ALTER TABLE products ADD COLUMN category VARCHAR(40) NOT NULL DEFAULT 'others'"))
        # Keep a useful starter catalogue available for shoppers, with all sample prices below 500 GMD.
        starter_products = [
            ("Rice (1 kg)", "food", "Everyday rice for home cooking.", 75, 35),
            ("Cooking oil (500 ml)", "food", "Cooking oil for daily meals.", 95, 28),
            ("Onions (1 kg)", "food", "Fresh onions for everyday cooking.", 45, 40),
            ("Potatoes (1 kg)", "food", "Potatoes for boiling, frying, and stews.", 60, 30),
            ("Sugar (1 kg)", "food", "Granulated sugar for drinks and baking.", 70, 24),
            ("Bread loaf", "food", "Fresh loaf for breakfast and snacks.", 35, 20),
            ("Eggs (6 pack)", "food", "Half-dozen eggs for home meals.", 90, 18),
            ("Spaghetti pack", "food", "Pasta for quick family meals.", 40, 32),
            ("Tomato paste", "food", "Tomato paste for sauces and stews.", 25, 45),
            ("Groundnuts (500 g)", "food", "Groundnuts for snacking or cooking.", 55, 22),
            ("Tea bags pack", "food", "Tea bags for hot drinks.", 85, 16),
            ("Biscuits pack", "food", "Biscuits for a quick snack.", 30, 38),
            ("LED bulb", "fixtures", "Energy-saving LED bulb for home lighting.", 120, 20),
            ("Door handle", "fixtures", "Simple replacement handle for interior doors.", 180, 12),
            ("Wall hook set", "fixtures", "Hooks for hanging light household items.", 65, 25),
            ("Power extension", "fixtures", "Multi-socket extension for home use.", 350, 10),
            ("Bathroom soap dish", "fixtures", "Compact soap dish for bathroom use.", 90, 18),
            ("Curtain tiebacks", "fixtures", "Pair of tiebacks for curtains.", 110, 14),
            ("Tap washer set", "fixtures", "Small replacement washers for common taps.", 45, 30),
            ("Cabinet knob", "fixtures", "Single replacement knob for cabinets.", 75, 24),
            ("Picture hook set", "fixtures", "Hooks for hanging lightweight frames.", 50, 22),
            ("Door stopper", "fixtures", "Rubber door stopper for home interiors.", 80, 17),
            ("Small wall mirror", "fixtures", "Compact mirror for a bedroom or hallway.", 450, 8),
            ("Shelf brackets (pair)", "fixtures", "Pair of brackets for a small wall shelf.", 300, 9),
            ("Notebook", "others", "Everyday notebook for school or work.", 40, 30),
            ("Ballpoint pens (3 pack)", "others", "Three pens for writing and note-taking.", 35, 35),
            ("Laundry detergent (small)", "others", "Detergent for routine laundry.", 125, 16),
            ("Bath towel", "others", "Soft everyday towel.", 250, 10),
            ("Reusable water bottle", "others", "Reusable bottle for water on the go.", 180, 14),
            ("Phone charging cable", "others", "USB charging cable for compatible devices.", 220, 12),
            ("Toothbrush", "others", "Basic toothbrush for daily oral care.", 30, 28),
            ("Washing sponge pack", "others", "Sponges for household cleaning.", 55, 24),
            ("Plastic storage container", "others", "Container for keeping small items organized.", 150, 13),
            ("Umbrella", "others", "Compact umbrella for rainy weather.", 400, 8),
            ("Shopping tote bag", "others", "Reusable bag for everyday shopping.", 100, 18),
            ("Sewing kit", "others", "Small kit for basic clothing repairs.", 160, 9),
        ]
        for name, category, description, price, stock in starter_products:
            product = models.Product.query.filter_by(name=name).first()
            if product is None:
                db.session.add(models.Product(
                    name=name, category=category, description=description,
                    price=Decimal(str(price)), stock_quantity=stock, is_active=True
                ))
        db.session.commit()

        if inspector.has_table("orders"):
            order_columns = {column["name"] for column in inspector.get_columns("orders")}
            if "collection_status" not in order_columns:
                with db.engine.begin() as connection:
                    connection.execute(text(
                        "ALTER TABLE orders ADD COLUMN collection_status VARCHAR(24) "
                        "NOT NULL DEFAULT 'not_collected'"
                    ))

    @app.get("/")
    def index():
        products = (
            db.session.query(models.Product)
            .join(ShopProduct, ShopProduct.product_id == models.Product.id)
            .join(Shop, Shop.id == ShopProduct.shop_id)
            .filter(models.Product.is_active.is_(True), Shop.status == "approved")
            .order_by(models.Product.created_at.desc())
            .all()
        )
        return render_template("index.html", products=products)

    @app.get("/products")
    def catalog():
        search = (request.args.get("q") or "").strip()
        category = (request.args.get("category") or "").strip().lower()
        allowed_categories = {"food", "fixtures", "others"}
        if category not in allowed_categories:
            category = ""
        query = db.session.query(models.Product).filter(
            models.Product.is_active.is_(True),
            models.Product.price < Decimal("500.00"),
        )
        if category:
            query = query.filter(models.Product.category == category)
        if search:
            pattern = f"%{search}%"
            query = query.filter(
                or_(
                    models.Product.name.ilike(pattern),
                    models.Product.description.ilike(pattern),
                )
            )
        products = query.order_by(models.Product.category.asc(), models.Product.name.asc()).all()
        return render_template("catalog.html", products=products, search=search, category=category)

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
        current_quantity = cart.get(product_id, 0)
        if current_quantity >= product.stock_quantity:
            flash("That product does not have enough stock available.", "error")
            return redirect(request.referrer or url_for("catalog"))
        cart[product_id] = current_quantity + 1
        session["cart"] = {str(product_id): quantity for product_id, quantity in cart.items()}
        flash(f"{product.name} added to your cart.", "success")
        return redirect(request.referrer or url_for("catalog"))

    @app.post("/cart/update/<int:product_id>")
    def update_cart(product_id):
        quantity = max(0, int(request.form.get("quantity", 0) or 0))
        product = models.Product.query.filter_by(id=product_id, is_active=True).first_or_404()
        if quantity > product.stock_quantity:
            flash(f"Only {product.stock_quantity} unit(s) of {product.name} are currently available.", "error")
            quantity = product.stock_quantity
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
        return render_template("checkout.html", items=items, total=total, branches=branches, customer_account=current_user if getattr(current_user, "is_customer", False) else None)


    def _json_request(url, payload, headers, method="POST"):
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=body, headers={"Content-Type":"application/json","Accept":"application/json",**headers}, method=method)
        with urllib.request.urlopen(req, timeout=12) as response:
            return json.loads(response.read().decode("utf-8"))


    def _wave_checkout(order, payer_phone):
        api_key = app.config.get("WAVE_API_KEY")
        if not api_key:
            raise RuntimeError("Wave payment is not configured.")
        mobile = payer_phone if payer_phone.startswith("+") else "+220" + payer_phone.lstrip("0")
        payload = {
            "amount": str(order.total.quantize(Decimal("0.01"))),
            "currency": app.config.get("WAVE_CURRENCY", "GMD"),
            "client_reference": order.order_number,
            "restrict_payer_mobile": mobile,
            "success_url": url_for("wave_payment_success", order_id=order.id, _external=True),
            "error_url": url_for("wave_payment_error", order_id=order.id, _external=True),
        }
        return _json_request("https://api.wave.com/v1/checkout/sessions", payload, {"Authorization":f"Bearer {api_key}"})


    def _aps_methods():
        token, secret, merchant = app.config.get("APS_APP_TOKEN"), app.config.get("APS_APP_SECRET"), app.config.get("APS_MERCHANT_GUID")
        if not token or not secret or not merchant:
            raise RuntimeError("APS payment is not configured.")
        base = app.config.get("APS_API_BASE","https://fpf-api.proc-gw.com").rstrip("/")
        req = urllib.request.Request(f"{base}/api/v3/{merchant}/info", headers={"X-App-Token":token,"X-App-Secret":secret,"Accept":"application/json"}, method="GET")
        with urllib.request.urlopen(req, timeout=12) as response:
            return json.loads(response.read().decode("utf-8"))


    def _aps_checkout(order, payer_phone, provider):
        methods = _aps_methods().get("methods", [])
        selected = next((m for m in methods if (provider=="yonna" and "yonna" in str(m.get("label","")).lower()) or (provider=="aps" and "aps" in str(m.get("label","")).lower())), None)
        if not selected:
            raise RuntimeError(f"{provider} is not available in the configured APS merchant account.")
        base = app.config.get("APS_API_BASE","https://fpf-api.proc-gw.com").rstrip("/")
        payload = {
            "mode":"deposit",
            "redirect_url":url_for("aps_payment_redirect",order_id=order.id,_external=True),
            "status_callback_url":url_for("aps_payment_callback",order_id=order.id,_external=True),
            "amount":float(order.total),
            "merchant_external_id":order.order_number,
            "merchant_payer_id":payer_phone,
            "payment_methods":[selected["guid"]],
        }
        return _json_request(f"{base}/api/v3/{app.config['APS_MERCHANT_GUID']}/fpf-url",payload,{"X-App-Token":app.config["APS_APP_TOKEN"],"X-App-Secret":app.config["APS_APP_SECRET"]})


    @app.post("/checkout")
    def submit_checkout():
        items = get_cart_items()
        if not items:
            flash("Your cart is empty.", "info")
            return redirect(url_for("catalog"))
        full_name = (request.form.get("full_name") or "").strip()
        phone_number = (request.form.get("phone_number") or "").strip()
        address = (request.form.get("address") or "").strip()
        # Branch IDs are integer primary keys; parse the submitted form value as an integer.
        branch_id = request.form.get("branch_id", type=int) or None
        provider = (request.form.get("provider") or "").strip().lower()
        payer_phone = (request.form.get("payer_phone_number") or "").strip()
        allowed = {"wave":"Wave","aps":"APS","yonna":"Yonna"}
        if not full_name or not phone_number or len(address) < 5 or len(address) > 300 or not payer_phone or provider not in allowed:
            flash("Please enter your full name, phone number, and address, select a payment method, and enter the payment phone number.", "error")
            return redirect(url_for("checkout"))

        # Check that the chosen provider is configured before creating customer,
        # order, and payment records. This prevents duplicate pending orders when
        # a shopper retries checkout while payment credentials are still missing.
        if provider == "wave" and not app.config.get("WAVE_API_KEY"):
            flash("Wave payments are not connected yet. Please contact the store before retrying.", "error")
            return redirect(url_for("checkout"))
        if provider == "aps" and not all(
            app.config.get(key) for key in ("APS_APP_TOKEN", "APS_APP_SECRET", "APS_MERCHANT_GUID")
        ):
            flash(f"{allowed[provider]} payments are not connected yet. Please contact the store before retrying.", "error")
            return redirect(url_for("checkout"))

        for item in items:
            if item["quantity"] > item["product"].stock_quantity:
                flash(f"Only {item['product'].stock_quantity} unit(s) of {item['product'].name} are currently available.", "error")
                return redirect(url_for("cart"))
        branch = models.Branch.query.filter_by(id=branch_id,is_active=True).first() if branch_id else None
        if getattr(current_user, "is_customer", False):
            customer = current_user
            customer.full_name = full_name
            customer.phone_number = phone_number
            customer.address = address
        else:
            customer = models.Customer(full_name=full_name, phone_number=phone_number, address=address)
            db.session.add(customer)
        db.session.flush()
        order_number = f"KORA-{datetime.utcnow().strftime('%Y%m%d')}-{(db.session.query(models.Order.id).count()+1):04d}"
        order = models.Order(order_number=order_number,customer=customer,branch=branch,status="pending",payment_status="pending",currency="GMD",total=Decimal("0.00"))
        db.session.add(order); db.session.flush()
        total = Decimal("0.00")
        for item in items:
            product, quantity = item["product"], item["quantity"]
            subtotal = product.price * quantity; total += subtotal
            db.session.add(models.OrderItem(order=order,product=product,product_name=product.name,quantity=quantity,unit_price=product.price,subtotal=subtotal))
        order.total = total
        payment = models.Payment(order=order,provider=allowed[provider],payer_phone_number=payer_phone,status="pending",amount=order.total)
        db.session.add(payment); db.session.commit()
        if provider == "yonna":
            # Yonna QR checkout is a manual-transfer flow. Keep the order unpaid
            # until a staff member verifies the money has arrived in the wallet.
            session["cart"] = {}
            return redirect(url_for("payment_pending", order_id=order.id))

        try:
            response = _wave_checkout(order,payer_phone) if provider=="wave" else _aps_checkout(order,payer_phone,provider)
            launch_url = response.get("wave_launch_url") if provider=="wave" else (response.get("how") or response.get("url"))
            payment.provider_reference = response.get("id") or response.get("transaction_id")
            db.session.commit()
        except Exception as exc:
            app.logger.exception("Payment initiation failed for %s: %s",order.order_number,exc)
            flash(f"{allowed[provider]} payment could not be started. The payment account is not connected yet.","error")
            return redirect(url_for("checkout"))
        if not launch_url:
            flash(f"{allowed[provider]} did not return a payment link.","error")
            return redirect(url_for("checkout"))
        session["cart"]={}
        return redirect(launch_url)


    def _ensure_receipt(order):
        """Create one receipt only after the payment provider confirms payment."""
        if order.receipt is not None:
            return order.receipt
        receipt = models.Receipt(
            order=order,
            receipt_number=f"REC-{order.order_number}",
            verification_code=secrets.token_hex(16),
        )
        db.session.add(receipt)
        return receipt


    @app.get("/payment/wave/success/<int:order_id>")
    def wave_payment_success(order_id):
        order=models.Order.query.get_or_404(order_id); api_key=app.config.get("WAVE_API_KEY")
        if not api_key:
            return redirect(url_for("payment_pending",order_id=order.id))
        try:
            url="https://api.wave.com/v1/checkout/sessions/search?client_reference="+urllib.parse.quote(order.order_number)
            data=_json_request(url,None,{"Authorization":f"Bearer {api_key}"},method="GET")
            result=(data.get("result") or [None])[0]
            if result and result.get("payment_status")=="succeeded":
                order.payment_status="paid"; order.status="confirmed"
                if order.payments:
                    order.payments[-1].status="paid"; order.payments[-1].provider_reference=result.get("id")
                _ensure_receipt(order)
                db.session.commit()
                return render_template("payment_pending.html",order=order,payment=order.payments[-1] if order.payments else None,provider_name="Wave",paid=True)
        except Exception:
            app.logger.exception("Wave payment verification failed")
        flash("Wave payment has not been confirmed yet.","info")
        return redirect(url_for("payment_pending",order_id=order.id))


    @app.get("/payment/wave/error/<int:order_id>")
    def wave_payment_error(order_id):
        order=models.Order.query.get_or_404(order_id)
        if order.payments: order.payments[-1].status="failed"
        db.session.commit()
        return redirect(url_for("payment_pending",order_id=order.id))


    @app.get("/payment/aps/redirect/<int:order_id>")
    def aps_payment_redirect(order_id):
        return redirect(url_for("payment_pending",order_id=order_id))


    @app.post("/payment/aps/callback/<int:order_id>")
    def aps_payment_callback(order_id):
        order=models.Order.query.get_or_404(order_id); raw=request.get_data(); secret=app.config.get("APS_CALLBACK_SECRET"); signature=request.headers.get("X-Signature")
        if secret and signature:
            expected=hmac.new(secret.encode("utf-8"),raw,hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected,signature): return {"error":"invalid signature"},401
        payload=request.get_json(silent=True) or {}; status=payload.get("status")
        if status=="done" or payload.get("sep31_status")=="completed":
            order.payment_status="paid"; order.status="confirmed"
            if order.payments: order.payments[-1].status="paid"; order.payments[-1].provider_reference=payload.get("transaction_id") or order.payments[-1].provider_reference
            _ensure_receipt(order)
        elif status in {"canceled","expired","refunded"} or payload.get("sep31_status")=="error":
            if order.payments: order.payments[-1].status="failed"
        db.session.commit(); return {"status":"ok"}


    @app.get("/payment/pending/<int:order_id>")
    def payment_pending(order_id):
        order=models.Order.query.get_or_404(order_id); payment=order.payments[-1] if order.payments else None
        if not payment: return redirect(url_for("checkout"))
        return render_template("payment_pending.html",order=order,payment=payment,provider_name=payment.provider,paid=order.payment_status=="paid")


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
