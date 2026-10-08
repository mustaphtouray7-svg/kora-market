import re
from decimal import Decimal

from store.models import (
    AdminUser,
    Branch,
    Customer,
    Order,
    OrderItem,
    Product,
)
from store.extensions import db


def test_homepage_and_health_endpoint(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Kora Market" in response.data
    assert client.get("/health").json == {"status": "ok"}


def test_admin_dashboard_requires_authentication(client):
    response = client.get("/admin")
    assert response.status_code == 302
    assert "/admin/login" in response.headers["Location"]


def test_admin_password_is_hashed_and_login_works(app, client):
    with app.app_context():
        admin = AdminUser(username="owner")
        admin.set_password("correct horse battery")
        db.session.add(admin)
        db.session.commit()
        assert admin.password_hash != "correct horse battery"
        assert admin.check_password("correct horse battery")
        assert not admin.check_password("incorrect password")

    response = client.post(
        "/admin/login",
        data={"username": "OWNER", "password": "correct horse battery"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Welcome back, owner." in response.data


def test_domain_schema_has_expected_foundation_tables(app):
    expected = {
        "admin_users",
        "branches",
        "customers",
        "products",
        "orders",
        "order_items",
        "payments",
        "receipts",
    }
    with app.app_context():
        assert expected.issubset(set(db.metadata.tables))


def test_database_initialization_and_order_models_persist(app):
    with app.app_context():
        existing_tables = set(db.inspect(db.engine).get_table_names())
        assert set(db.metadata.tables).issubset(existing_tables)

        branch = Branch(name="Central")
        customer = Customer(full_name="Aminata Jallow", phone_number="1234567")
        product = Product(name="Market basket", price=Decimal("125.00"))
        order = Order(
            order_number="KM-TEST-001",
            customer=customer,
            branch=branch,
            total=Decimal("250.00"),
        )
        order.items.append(
            OrderItem(
                product=product,
                product_name=product.name,
                quantity=2,
                unit_price=Decimal("125.00"),
                subtotal=Decimal("250.00"),
            )
        )
        db.session.add(order)
        db.session.commit()

        saved_order = db.session.get(Order, order.id)
        assert saved_order.customer.full_name == "Aminata Jallow"
        assert saved_order.branch.name == "Central"
        assert saved_order.items[0].product.name == "Market basket"
        assert saved_order.total == Decimal("250.00")
        assert saved_order.payment_status == "pending"


def test_login_and_logout_require_valid_csrf_tokens(app, client):
    app.config["WTF_CSRF_ENABLED"] = True
    with app.app_context():
        admin = AdminUser(username="owner")
        admin.set_password("correct horse battery")
        db.session.add(admin)
        db.session.commit()

    login_page = client.get("/admin/login")
    token_match = re.search(
        rb'name="csrf_token" type="hidden" value="([^"]+)"', login_page.data
    )
    assert token_match is not None
    csrf_token = token_match.group(1).decode()

    rejected_login = client.post(
        "/admin/login",
        data={"username": "owner", "password": "correct horse battery"},
    )
    assert rejected_login.status_code == 400

    dashboard = client.post(
        "/admin/login",
        data={
            "csrf_token": csrf_token,
            "username": "owner",
            "password": "correct horse battery",
        },
        follow_redirects=True,
    )
    assert dashboard.status_code == 200
    assert b"Welcome back, owner." in dashboard.data

    rejected_logout = client.post("/admin/logout")
    assert rejected_logout.status_code == 400

    logout = client.post(
        "/admin/logout",
        data={"csrf_token": csrf_token},
    )
    assert logout.status_code == 302
    assert "/admin/login" in logout.headers["Location"]


def test_public_storefront_lists_only_active_products(app, client):
    with app.app_context():
        active = Product(name="Fresh rice", price=Decimal("50.00"), is_active=True)
        inactive = Product(name="Old stock", price=Decimal("40.00"), is_active=False)
        db.session.add_all([active, inactive])
        db.session.commit()
        active_id = active.id
        inactive_id = inactive.id

    response = client.get("/")
    assert response.status_code == 200
    assert b"Fresh rice" in response.data
    assert b"Old stock" not in response.data

    catalog = client.get("/products")
    assert catalog.status_code == 200
    assert b"Fresh rice" in catalog.data
    assert b"Old stock" not in catalog.data

    detail = client.get(f"/products/{active_id}")
    assert detail.status_code == 200
    assert b"Fresh rice" in detail.data

    inactive_detail = client.get(f"/products/{inactive_id}")
    assert inactive_detail.status_code == 404
