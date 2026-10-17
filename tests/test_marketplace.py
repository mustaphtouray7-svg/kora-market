from store.extensions import db
from store.marketplace_models import Seller, Shop
from store.models import AdminUser, Product\nfrom store.marketplace_models import ShopProduct


def test_seller_can_apply_but_pending_shop_is_not_public(client, app):
    response = client.post(
        "/seller/register",
        data={
            "full_name": "Awa Jallow",
            "email": "awa@example.com",
            "phone_number": "7771234",
            "shop_name": "Awa Fashion",
            "category": "Fashion",
            "description": "Clothing and accessories for everyday use.",
            "password": "secure-password-123",
            "confirm_password": "secure-password-123",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"application was submitted" in response.data
    assert b"Pending" in response.data

    with app.app_context():
        seller = Seller.query.filter_by(email="awa@example.com").one()
        shop = Shop.query.filter_by(owner_id=seller.id).one()
        assert shop.status == "pending"
        assert shop.slug == "awa-fashion"

    directory = client.get("/shops")
    assert directory.status_code == 200
    assert b"Awa Fashion" not in directory.data


def test_only_admin_can_approve_shop_and_approved_shop_becomes_public(client, app):
    with app.app_context():
        admin = AdminUser(username="owner@example.com")
        admin.set_password("correct horse battery")
        seller = Seller(
            full_name="Musa Store",
            email="musa@example.com",
            phone_number="7770000",
        )
        seller.set_password("secure-password-123")
        shop = Shop(
            owner=seller,
            name="Musa Store",
            slug="musa-store",
            category="Electronics",
            description="Phones and accessories for customers.",
            contact_phone="7770000",
            status="pending",
        )
        db.session.add_all([admin, seller, shop])
        db.session.commit()
        shop_id = shop.id

    forbidden = client.post(f"/admin/shops/{shop_id}/review", data={"decision": "approve"})
    assert forbidden.status_code in (302, 401, 403)

    login = client.post(
        "/admin/login",
        data={"username": "owner@example.com", "password": "correct horse battery"},
        follow_redirects=True,
    )
    assert login.status_code == 200

    approved = client.post(
        f"/admin/shops/{shop_id}/review",
        data={"decision": "approve", "admin_note": "Application reviewed."},
        follow_redirects=True,
    )
    assert approved.status_code == 200
    assert b"approved" in approved.data.lower()

    public_page = client.get("/shops")
    assert b"Musa Store" in public_page.data
    detail = client.get("/shops/musa-store")
    assert detail.status_code == 200
    assert b"Application reviewed" not in detail.data



def test_product_can_be_assigned_to_one_shop(app):
    with app.app_context():
        seller = Seller(
            full_name="Fatou Jobe",
            email="fatou@example.com",
            phone_number="7775555",
        )
        seller.set_password("secure-password-123")
        shop = Shop(
            owner=seller,
            name="Fatou Home",
            slug="fatou-home",
            category="Home",
            description="Home goods and household items.",
            contact_phone="7775555",
            status="approved",
        )
        product = Product(name="Storage basket", price=125)
        db.session.add_all([seller, shop, product])
        db.session.flush()
        link = ShopProduct(shop=shop, product=product)
        db.session.add(link)
        db.session.commit()

        saved_link = ShopProduct.query.filter_by(product_id=product.id).one()
        assert saved_link.shop.name == "Fatou Home"
        assert saved_link.product.name == "Storage basket"
