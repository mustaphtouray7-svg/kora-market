from store.extensions import db
from store.marketplace_models import Seller, Shop
from store.models import AdminUser


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
        admin = AdminUser(username="owner")
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
        data={"username": "owner", "password": "correct horse battery"},
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
