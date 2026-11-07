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
from store.waychit import create_payment_request as create_waychit_payment_request, extract_payment_request, verify_waychit_signature


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


    # Curated product photos for the starter catalogue. Seller-provided image URLs
    # remain preferred; these photos keep sample products from showing placeholders.
    product_photo_ids = {
        "Rice (1 kg)": "photo-1586201375761-83865001e31c",
        "Cooking oil (500 ml)": "photo-1474979266404-7eaacbcd87c5",
        "Onions (1 kg)": "photo-1518977676601-b53f82aba655",
        "Potatoes (1 kg)": "photo-1518977676601-b53f82aba655",
        "Sugar (1 kg)": "photo-1581441363689-1f3c3c414635",
        "Bread loaf": "photo-1509440159596-0249088772ff",
        "Eggs (6 pack)": "photo-1518569656558-1f25e69d93d7",
        "Spaghetti pack": "photo-1551183053-bf91a1d81141",
        "Tomato paste": "photo-1546094096-0df4bcaaa337",
        "Groundnuts (500 g)": "photo-1567892737950-30c4db37cd89",
        "Tea bags pack": "photo-1544787219-7f47ccb76574",
        "Biscuits pack": "photo-1499636136210-6f4ee915583e",
        "LED bulb": "photo-1507473885765-e6ed057f782c",
        "Door handle": "photo-1600607687939-ce8a6c25118c",
        "Wall hook set": "photo-1600566753086-00f18fb6b3ea",
        "Power extension": "photo-1558002038-1055907df827",
        "Bathroom soap dish": "photo-1620626011761-996317b8d101",
        "Curtain tiebacks": "photo-1600210492486-724fe5c67fb0",
        "Tap washer set": "photo-1621905251189-08b45d6a269e",
        "Cabinet knob": "photo-1616486338812-3dadae4b4ace",
        "Picture hook set": "photo-1600566753190-17f0baa2a6c3",
        "Door stopper": "photo-1600607687920-4e2a09cf159d",
        "Small wall mirror": "photo-1618221195710-dd6b41faaea6",
        "Shelf brackets (pair)": "photo-1600585154340-be6161a56a0c",
        "Notebook": "photo-1512820790803-83ca734da794",
        "Ballpoint pens (3 pack)": "photo-1455390582262-044cdeb277a6",
        "Laundry detergent (small)": "photo-1608248543803-ba4f8c70ae0b",
        "Bath towel": "photo-1600369671236-e74521d4b6ad",
        "Reusable water bottle": "photo-1602143407151-7111542de6e8",
        "Phone charging cable": "photo-1583863788434-e58a36330cf0",
        "Toothbrush": "photo-1609840114035-3c981b782dfe",
        "Washing sponge pack": "photo-1585421514284-efb74c2b69ba",
        "Plastic storage container": "photo-1583947581924-860bda6a26df",
        "Umbrella": "photo-1534274988757-a28bf1a57c17",
        "Shopping tote bag": "photo-1590874103328-eac38a683ce7",
        "Sewing kit": "photo-1604881988758-f76ad2f7aac1",
    }

    def product_photo(product, width=640):
        if product.image_url:
            return product.image_url
        photo_id = product_photo_ids.get(product.name)
        if photo_id:
            return f"https://images.unsplash.com/{photo_id}?auto=format&fit=crop&w={width}&q=82"
        # Stable category-specific fallback for products added later.
        fallback_ids = {
            "food": "photo-1542838132-92c53300491e",
            "fixtures": "photo-1600607687939-ce8a6c25118c",
            "others": "photo-1583947581924-860bda6a26df",
        }
        return f"https://images.unsplash.com/{fallback_ids.get(product.category, fallback_ids['others'])}?auto=format&fit=crop&w={width}&q=82"

    app.jinja_env.globals["product_photo"] = product_photo

    @app.context_processor
    def inject_marketplace_shops():
        return {
            "approved_shops": Shop.query.filter_by(status="approved")
            .order_by(Shop.name.asc()).limit(6).all()
        }

    # Create the staff table and safely add the pickup-status column to existing orders.
    # Inspection keeps this migration compatible with both PostgreSQL and SQLite.
    with app.app_context():
        db.metadata.create_all(bind=db.engine)
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
        # Demo fashion storefronts with sample dress inventory. The seed is idempotent:
        # existing seller accounts, brands, products, and product links are left intact.
        demo_fashion_brands = [
            {
                "name": "Kaira Couture",
                "slug": "kaira-couture",
                "email": "kaira-couture-demo@gam-market.invalid",
                "description": "African Muslim fashion focused exclusively on modest dresses, including flowing boubous, Ankara abayas, and elegant kaftans.",
                "phone": "Seller contact to be added",
                "image": "https://images.unsplash.com/photo-1490481651871-ab68de25d43d?auto=format&fit=crop&w=900&q=82",
                "dresses": [
                    ("Kaira Floral Midi Dress", "Kaira African Boubou Dress", "A flowing African boubou designed for modest coverage and comfortable everyday wear.", 450, "photo-1496747611176-843222e1e57c"),
                    ("Kaira Classic Evening Dress", "Kaira Embroidered Kaftan", "An elegant modest kaftan for Eid, weddings, and other special occasions.", 480, "photo-1566174053879-31528523f8ae"),
                    ("Kaira Everyday Dress", "Kaira Ankara Abaya Dress", "A modest abaya-inspired dress with African Ankara styling.", 350, "photo-1539008835657-9e8e9680c956"),
                ],
            },
            {
                "name": "Ndey Dress Boutique",
                "slug": "ndey-dress-boutique",
                "email": "ndey-dress-boutique-demo@gam-market.invalid",
                "description": "A sample boutique for colourful dresses, smart casual looks, and celebration outfits.",
                "phone": "Seller contact to be added",
                "image": "https://images.unsplash.com/photo-1483985988355-763728e1935b?auto=format&fit=crop&w=900&q=82",
                "dresses": [
                    ("Ndey Smart Casual Dress", "Ndey Smart Casual Dress", "A smart casual dress suitable for outings and gatherings.", 400, "photo-1483985988355-763728e1935b"),
                    ("Ndey Occasion Dress", "Ndey Occasion Dress", "A dress for parties, celebrations, and events.", 475, "photo-1515886657613-9f3515b0c78f"),
                    ("Ndey Printed Day Dress", "Ndey Printed Day Dress", "A printed day dress for everyday wear.", 325, "photo-1485230895905-ec40ba36b9bc"),
                ],
            },
        ]
        for brand_data in demo_fashion_brands:
            seller = Seller.query.filter_by(email=brand_data["email"]).first()
            if seller is None:
                seller = Seller(
                    full_name=brand_data["name"],
                    email=brand_data["email"],
                    phone_number=brand_data["phone"],
                )
                seller.set_password(secrets.token_urlsafe(32))
                db.session.add(seller)
                db.session.flush()
            shop = Shop.query.filter_by(slug=brand_data["slug"]).first()
            if shop is None:
                shop = Shop(
                    owner=seller,
                    name=brand_data["name"],
                    slug=brand_data["slug"],
                    description=brand_data["description"],
                    image_data=brand_data["image"],
                    category="Fashion",
                    contact_phone=brand_data["phone"],
                    status="approved",
                    reviewed_at=datetime.utcnow(),
                )
                db.session.add(shop)
                db.session.flush()
            else:
                shop.description = brand_data["description"]
            for old_name, dress_name, dress_description, dress_price, dress_photo in brand_data["dresses"]:
                dress = models.Product.query.filter_by(name=dress_name).first()
                if dress is None and old_name != dress_name:
                    # Rename previously seeded Kaira sample dresses instead of duplicating them.
                    dress = models.Product.query.filter_by(name=old_name).first()
                if dress is None:
                    dress = models.Product(
                        name=dress_name,
                        category="others",
                        description=dress_description,
                        price=Decimal(str(dress_price)),
                        image_url=f"https://images.unsplash.com/{dress_photo}?auto=format&fit=crop&w=800&q=82",
                        stock_quantity=8,
                        is_active=True,
                    )
                    db.session.add(dress)
                    db.session.flush()
                else:
                    dress.name = dress_name
                    dress.description = dress_description
                    dress.price = Decimal(str(dress_price))
                    dress.image_url = f"https://images.unsplash.com/{dress_photo}?auto=format&fit=crop&w=800&q=82"
                    dress.is_active = True
                if ShopProduct.query.filter_by(shop_id=shop.id, product_id=dress.id).first() is None:
                    # Product can belong to only one shop; do not reassign products owned elsewhere.
                    if not ShopProduct.query.filter_by(product_id=dress.id).first():
                        db.session.add(ShopProduct(shop_id=shop.id, product_id=dress.id))
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


    def _waychit_checkout(order):
        api_key = app.config.get("WAYCHIT_API_KEY")
        if not api_key:
            raise RuntimeError("Waychit payment is not configured.")
        return create_waychit_payment_request(
            api_key=api_key,
            api_base=app.config.get("WAYCHIT_API_BASE", "https://api.waychit.com"),
            amount=str(order.total.quantize(Decimal("0.01"))),
            description=f"GAM-MARKET order {order.order_number}",
            client_reference=order.order_number,
            success_redirect_url=url_for("waychit_payment_return", order_id=order.id, _external=True),
            failure_redirect_url=url_for("waychit_payment_return", order_id=order.id, _external=True),
            metadata={"orderId": order.id, "orderNumber": order.order_number},
        )


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
        allowed = {"wave":"Wave","aps":"APS","yonna":"Yonna","waychit":"Waychit"}
        if not full_name or not phone_number or len(address) < 5 or len(address) > 300 or not payer_phone or provider not in allowed:
            flash("Please enter your full name, phone number, and address, select a payment method, and enter the payment phone number.", "error")
            return redirect(url_for("checkout"))

        # Check that the chosen provider is configured before creating customer,
        # order, and payment records. This prevents duplicate pending orders when
        # a shopper retries checkout while payment credentials are still missing.
        if provider == "waychit" and not app.config.get("WAYCHIT_API_KEY"):
            flash("Waychit payments are not connected yet. Please contact the store before retrying.", "error")
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
        # Keep this order available to the customer who created it, including
        # after returning from the external wallet payment page.
        accessible_orders = session.get("accessible_order_ids", [])
        if order.id not in accessible_orders:
            session["accessible_order_ids"] = (accessible_orders + [order.id])[-20:]
        if provider == "yonna" or (provider == "wave" and not app.config.get("WAVE_API_KEY")):
            # Manual wallet-link payments remain unpaid until an administrator
            # verifies the transaction in the merchant wallet.
            session["cart"] = {}
            return redirect(url_for("payment_pending", order_id=order.id))

        try:
            if provider == "waychit":
                response = _waychit_checkout(order)
                request_data = extract_payment_request(response)
                launch_url = request_data.get("waychitLaunchUrl") or request_data.get("launchUrl") or request_data.get("url")
                payment.provider_reference = request_data.get("id") or request_data.get("paymentRequestId")
            else:
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


    def _confirm_wave_checkout(order, checkout):
        """Confirm only the matching Wave checkout after validating its fields."""
        if not isinstance(checkout, dict):
            return False
        payment = (
            models.Payment.query
            .filter_by(order_id=order.id, provider="Wave")
            .order_by(models.Payment.created_at.desc())
            .first()
        )
        if payment is None:
            return False
        if checkout.get("client_reference") != order.order_number:
            return False
        if payment.provider_reference and checkout.get("id") != payment.provider_reference:
            return False
        if str(checkout.get("payment_status", "")).lower() != "succeeded":
            return False
        try:
            paid_amount = Decimal(str(checkout.get("amount"))).quantize(Decimal("0.01"))
            expected_amount = Decimal(str(payment.amount)).quantize(Decimal("0.01"))
        except Exception:
            return False
        if paid_amount != expected_amount:
            return False
        if str(checkout.get("currency", "")).upper() != order.currency.upper():
            return False

        if order.payment_status != "paid":
            payment.status = "paid"
            payment.provider_reference = checkout.get("id") or payment.provider_reference
            order.payment_status = "paid"
            order.status = "confirmed"
            _ensure_receipt(order)
            db.session.commit()
        return True


    def _verify_wave_webhook_signature(signature, raw_body, secret):
        """Verify Wave's timestamped HMAC-SHA256 webhook signature."""
        if not signature or not secret:
            return False
        try:
            parts = [part.strip() for part in signature.split(",")]
            timestamps = [part[2:] for part in parts if part.startswith("t=")]
            signatures = [part[3:] for part in parts if part.startswith("v1=")]
            if len(timestamps) != 1 or not signatures:
                return False
            timestamp = int(timestamps[0])
            now = int(datetime.utcnow().timestamp())
            if timestamp < now - 300 or timestamp > now + 30:
                return False
            expected = hmac.new(
                secret.encode("utf-8"),
                str(timestamp).encode("ascii") + raw_body,
                hashlib.sha256,
            ).hexdigest()
            return any(hmac.compare_digest(expected, item) for item in signatures)
        except (TypeError, ValueError, UnicodeError):
            return False


    @app.get("/payment/wave/success/<int:order_id>")
    def wave_payment_success(order_id):
        order = models.Order.query.get_or_404(order_id)
        api_key = app.config.get("WAVE_API_KEY")
        if not api_key:
            return redirect(url_for("payment_pending", order_id=order.id))
        try:
            url = "https://api.wave.com/v1/checkout/sessions/search?client_reference=" + urllib.parse.quote(order.order_number)
            data = _json_request(url, None, {"Authorization": f"Bearer {api_key}"}, method="GET")
            payment = (
                models.Payment.query
                .filter_by(order_id=order.id, provider="Wave")
                .order_by(models.Payment.created_at.desc())
                .first()
            )
            expected_session_id = payment.provider_reference if payment else None
            result = next(
                (
                    item for item in (data.get("result") or [])
                    if item.get("client_reference") == order.order_number
                    and (not expected_session_id or item.get("id") == expected_session_id)
                ),
                None,
            )
            if result and _confirm_wave_checkout(order, result):
                return render_template(
                    "payment_pending.html", order=order, payment=payment,
                    provider_name="Wave", paid=True,
                )
        except Exception:
            app.logger.exception("Wave payment verification failed")
        flash("Wave payment has not been confirmed yet.", "info")
        return redirect(url_for("payment_pending", order_id=order.id))


    @app.get("/payment/wave/error/<int:order_id>")
    def wave_payment_error(order_id):
        order = models.Order.query.get_or_404(order_id)
        # A browser redirect is not authoritative proof of a failed payment.
        # Leave the payment pending until Wave's API or signed webhook confirms it.
        return redirect(url_for("payment_pending", order_id=order.id))


    @app.post("/payment/wave/webhook")
    @csrf.exempt
    def wave_payment_webhook():
        secret = app.config.get("WAVE_WEBHOOK_SECRET")
        if not secret:
            return {"error": "Wave webhook verification is not configured"}, 503

        raw_body = request.get_data()
        signature = request.headers.get("Wave-Signature", "")
        if not _verify_wave_webhook_signature(signature, raw_body, secret):
            return {"error": "invalid signature"}, 401

        try:
            event = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {"error": "invalid JSON"}, 400

        event_type = event.get("type")
        data = event.get("data") or {}
        if event_type not in {"checkout.session.completed", "checkout.session.payment_failed"}:
            return {"status": "ignored"}, 200

        client_reference = data.get("client_reference")
        if not client_reference:
            return {"error": "missing client reference"}, 400
        order = models.Order.query.filter_by(order_number=client_reference).first()
        if order is None:
            return {"error": "order not found"}, 404

        payment = (
            models.Payment.query
            .filter_by(order_id=order.id, provider="Wave")
            .order_by(models.Payment.created_at.desc())
            .first()
        )
        if payment is None:
            return {"error": "Wave payment not found"}, 404

        if event_type == "checkout.session.completed":
            if not _confirm_wave_checkout(order, data):
                return {"error": "checkout details do not match pending order"}, 409
        else:
            # Failure events can occur before a later successful attempt. Do not
            # downgrade an already-paid order or permanently close its payment.
            if order.payment_status != "paid" and data.get("id") == payment.provider_reference:
                payment.status = "failed"
                db.session.commit()

        return {"status": "ok"}, 200


    @app.get("/payment/aps/redirect/<int:order_id>")
    def aps_payment_redirect(order_id):
        return redirect(url_for("payment_pending",order_id=order_id))


    @app.post("/payment/aps/callback/<int:order_id>")
    @csrf.exempt
    def aps_payment_callback(order_id):
        # APS signs the exact raw JSON body with HMAC-SHA256. Never accept an
        # unsigned callback: otherwise anyone who knows an order ID could mark
        # an unpaid order as paid.
        secret = app.config.get("APS_CALLBACK_SECRET")
        if not secret:
            return {"error": "APS callback verification is not configured"}, 503

        raw = request.get_data()
        signature = (request.headers.get("X-Signature") or "").strip()
        expected = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
        if not signature or not hmac.compare_digest(expected, signature):
            return {"error": "invalid signature"}, 401

        payload = request.get_json(silent=True) or {}
        order = models.Order.query.get_or_404(order_id)
        payment = (
            models.Payment.query
            .filter(
                models.Payment.order_id == order.id,
                models.Payment.provider == "APS",
            )
            .order_by(models.Payment.created_at.desc())
            .first()
        )
        if payment is None:
            return {"error": "matching payment not found"}, 404

        # Bind the callback to the exact order and payment transaction.
        external_id = payload.get("merchant_external_id")
        if external_id != order.order_number:
            return {"error": "order reference mismatch"}, 409
        transaction_id = payload.get("transaction_id")

        status = str(payload.get("status") or "").lower()
        sep31_status = str(payload.get("sep31_status") or "").lower()
        refunded = payload.get("refunded") is True

        if status == "done" and sep31_status == "completed" and not refunded:
            amount_value = payload.get("amount_in")
            if amount_value is None:
                return {"error": "payment amount missing"}, 400
            try:
                confirmed_amount = Decimal(str(amount_value)).quantize(Decimal("0.01"))
            except Exception:
                return {"error": "invalid payment amount"}, 400
            expected_amount = Decimal(str(payment.amount)).quantize(Decimal("0.01"))
            if confirmed_amount != expected_amount:
                return {"error": "payment amount mismatch"}, 409
            callback_currency = str(payload.get("body_currency") or "").upper()
            if callback_currency:
                currency_code = callback_currency.rsplit(":", 1)[-1]
                if currency_code != order.currency.upper():
                    return {"error": "payment currency mismatch"}, 409

            payment.status = "paid"
            if transaction_id:
                payment.provider_reference = transaction_id
            order.payment_status = "paid"
            order.status = "confirmed"
            _ensure_receipt(order)
        elif status in {"canceled", "expired", "refunded"} or sep31_status == "error" or refunded:
            payment.status = "failed"

        db.session.commit()
        return {"status": "ok"}



    @app.get("/payment/waychit/return/<int:order_id>")
    def waychit_payment_return(order_id):
        # Redirects are only a customer navigation step. The signed webhook is
        # the payment confirmation source; never mark an order paid on redirect.
        order = models.Order.query.get_or_404(order_id)
        return redirect(url_for("payment_pending", order_id=order.id))


    @app.post("/payment/waychit/webhook")
    @csrf.exempt
    def waychit_payment_webhook():
        secret = app.config.get("WAYCHIT_WEBHOOK_SECRET")
        if not secret:
            return {"error": "webhook verification is not configured"}, 503
        raw_body = request.get_data()
        signature = request.headers.get("Waychit-Signature", "")
        if not verify_waychit_signature(signature, raw_body, secret):
            return {"error": "invalid signature"}, 401

        payload = request.get_json(silent=True) or {}
        if payload.get("type") != "payment.request.completed":
            return {"status": "ignored"}, 200

        data = payload.get("data") or {}
        client_reference = data.get("clientReference") or data.get("client_reference")
        metadata = data.get("metadata") or {}
        order_number = client_reference or metadata.get("orderNumber")
        order_id = metadata.get("orderId")
        order = models.Order.query.filter_by(order_number=order_number).first() if order_number else None
        if order is None and str(order_id or "").isdigit():
            order = db.session.get(models.Order, int(order_id))
        if order is None:
            return {"status": "unmatched"}, 200

        payment = (models.Payment.query
                   .filter_by(order_id=order.id, provider="Waychit")
                   .order_by(models.Payment.created_at.desc())
                   .first())
        if payment is None or order.payment_status == "paid":
            return {"status": "already_processed" if order.payment_status == "paid" else "unmatched"}, 200

        request_id = data.get("id") or data.get("paymentRequestId")
        if payment.provider_reference and request_id and payment.provider_reference != request_id:
            return {"error": "payment reference mismatch"}, 409

        # Only a signed, completed event with a matching reference and amount
        # can settle an order. Unknown/incomplete payloads are acknowledged but
        # never treated as successful payments.
        status = str(data.get("status") or "").lower()
        amount_value = data.get("amount")
        if status not in {"completed", "succeeded", "paid"} or amount_value is None:
            return {"status": "not_final"}, 200
        try:
            confirmed_amount = Decimal(str(amount_value)).quantize(Decimal("0.01"))
        except Exception:
            return {"error": "invalid amount"}, 400
        expected_amount = Decimal(str(payment.amount)).quantize(Decimal("0.01"))
        if confirmed_amount != expected_amount:
            return {"error": "amount mismatch"}, 409
        if data.get("currency") and str(data["currency"]).upper() != order.currency.upper():
            return {"error": "currency mismatch"}, 409

        payment.status = "paid"
        if request_id:
            payment.provider_reference = request_id
        order.payment_status = "paid"
        order.status = "confirmed"
        _ensure_receipt(order)
        db.session.commit()
        return {"status": "processed"}, 200

    @app.post("/payment/wave/reference/<int:order_id>")
    def submit_wave_payment_reference(order_id):
        order = models.Order.query.get_or_404(order_id)
        payment = (
            models.Payment.query
            .filter_by(order_id=order.id, provider="Wave")
            .order_by(models.Payment.created_at.desc())
            .first()
        )
        if payment is None:
            return {"error": "Wave payment not found"}, 404
        if order.payment_status == "paid":
            flash("This order has already been paid.", "info")
            return redirect(url_for("payment_pending", order_id=order.id))
        reference = (request.form.get("wave_reference") or "").strip()
        if not reference or len(reference) > 160:
            flash("Enter the Wave transaction reference from your payment receipt.", "error")
            return redirect(url_for("payment_pending", order_id=order.id))
        # A Wave transaction reference must belong to only one payment.
        # Reject reuse cleanly instead of allowing the database uniqueness
        # constraint to turn a customer typo/retry into an HTTP 500.
        existing_payment = models.Payment.query.filter(
            models.Payment.provider_reference == reference,
            models.Payment.id != payment.id,
        ).first()
        if existing_payment is not None:
            flash(
                "This Wave transaction reference has already been submitted. "
                "Check the reference on your Wave receipt and enter the correct one.",
                "error",
            )
            return redirect(url_for("payment_pending", order_id=order.id))
        payment.provider_reference = reference
        payment.status = "submitted"
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            app.logger.exception("Could not save Wave reference for order %s", order.order_number)
            flash("We could not save that reference. Please check it and try again.", "error")
            return redirect(url_for("payment_pending", order_id=order.id))
        flash("Your Wave transaction reference was received. Payment is awaiting verification.", "success")
        return redirect(url_for("order_receipt", order_id=order.id))

    @app.get("/orders/<int:order_id>/receipt")
    def order_receipt(order_id):
        order = models.Order.query.get_or_404(order_id)
        accessible_orders = session.get("accessible_order_ids", [])
        if order.id not in accessible_orders and not getattr(current_user, "is_admin", False):
            raise NotFound()
        payment = (
            models.Payment.query
            .filter_by(order_id=order.id)
            .order_by(models.Payment.created_at.desc())
            .first()
        )
        return render_template(
            "order_receipt.html",
            order=order,
            payment=payment,
            receipt=order.receipt,
            paid=order.payment_status == "paid",
        )

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
