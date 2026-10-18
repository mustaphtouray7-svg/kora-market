from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from store.extensions import db
from store.models import Product


def utc_now():
    return datetime.now(timezone.utc)


class Seller(UserMixin, db.Model):
    """A seller account that can own one or more approved marketplace shops."""

    __tablename__ = "sellers"

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(160), nullable=False)
    email = db.Column(db.String(254), nullable=False, unique=True, index=True)
    phone_number = db.Column(db.String(32), nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)

    shops = db.relationship("Shop", back_populates="owner", cascade="all, delete-orphan")

    @property
    def is_admin(self):
        return False

    @property
    def is_customer(self):
        return False

    @property
    def is_seller(self):
        return True

    def get_id(self):
        return f"seller:{self.id}"

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Shop(db.Model):
    """A shop application and public storefront, pending administrator approval by default."""

    __tablename__ = "shops"

    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("sellers.id"), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    slug = db.Column(db.String(140), nullable=False, unique=True, index=True)
    description = db.Column(db.String(1200), nullable=False, default="")
    image_data = db.Column(db.Text)
    category = db.Column(db.String(80), nullable=False)
    contact_phone = db.Column(db.String(32), nullable=False)
    status = db.Column(db.String(24), nullable=False, default="pending", index=True)
    admin_note = db.Column(db.String(1000))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    reviewed_at = db.Column(db.DateTime(timezone=True))

    owner = db.relationship("Seller", back_populates="shops")
    product_links = db.relationship("ShopProduct", back_populates="shop", cascade="all, delete-orphan")

    @property
    def is_public(self):
        return self.status == "approved"


class ShopProduct(db.Model):
    """Connect a catalog product to its shop; each product belongs to at most one shop."""

    __tablename__ = "shop_products"

    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shops.id"), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False, unique=True, index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)

    shop = db.relationship("Shop", back_populates="product_links")
    product = db.relationship(Product)
