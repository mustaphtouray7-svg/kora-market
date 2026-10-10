from datetime import datetime, timezone
from decimal import Decimal

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from store.extensions import db, login_manager


def utc_now():
    return datetime.now(timezone.utc)


class AdminUser(UserMixin, db.Model):
    __tablename__ = "admin_users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)

    @property
    def is_admin(self):
        """Existing AdminUser accounts have administrator permissions."""
        return True

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class StaffUser(UserMixin, db.Model):
    __tablename__ = "staff_users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(254), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)

    @property
    def is_admin(self):
        return False

    def get_id(self):
        return f"staff:{self.id}"

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Branch(db.Model):
    __tablename__ = "branches"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    address = db.Column(db.String(300))
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    orders = db.relationship("Order", back_populates="branch")


class Customer(UserMixin, db.Model):
    __tablename__ = "customers"

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(160), nullable=False)
    phone_number = db.Column(db.String(32), nullable=False, index=True)
    email = db.Column(db.String(254), unique=True, index=True, nullable=True)
    password_hash = db.Column(db.String(256), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    orders = db.relationship("Order", back_populates="customer")

    @property
    def is_admin(self):
        return False

    @property
    def is_customer(self):
        return True

    def get_id(self):
        return f"customer:{self.id}"

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return bool(self.password_hash) and check_password_hash(self.password_hash, password)


class Product(db.Model):
    __tablename__ = "products"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False, index=True)
    description = db.Column(db.Text, nullable=False, default="")
    price = db.Column(db.Numeric(12, 2), nullable=False)
    image_url = db.Column(db.String(500))
    stock_quantity = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)


class Order(db.Model):
    __tablename__ = "orders"

    id = db.Column(db.Integer, primary_key=True)
    order_number = db.Column(db.String(40), nullable=False, unique=True, index=True)
    customer_id = db.Column(
        db.Integer, db.ForeignKey("customers.id"), nullable=False, index=True
    )
    branch_id = db.Column(db.Integer, db.ForeignKey("branches.id"), nullable=True)
    status = db.Column(db.String(32), nullable=False, default="pending", index=True)
    collection_status = db.Column(db.String(24), nullable=False, default="not_collected", index=True)
    payment_status = db.Column(
        db.String(32), nullable=False, default="pending", index=True
    )
    currency = db.Column(db.String(3), nullable=False, default="GMD")
    total = db.Column(db.Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)

    customer = db.relationship("Customer", back_populates="orders")
    branch = db.relationship("Branch", back_populates="orders")
    items = db.relationship(
        "OrderItem", back_populates="order", cascade="all, delete-orphan"
    )
    payments = db.relationship(
        "Payment", back_populates="order", cascade="all, delete-orphan"
    )
    receipt = db.relationship(
        "Receipt", back_populates="order", uselist=False, cascade="all, delete-orphan"
    )


class OrderItem(db.Model):
    __tablename__ = "order_items"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(
        db.Integer, db.ForeignKey("orders.id"), nullable=False, index=True
    )
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=True)
    product_name = db.Column(db.String(160), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(db.Numeric(12, 2), nullable=False)
    subtotal = db.Column(db.Numeric(12, 2), nullable=False)

    order = db.relationship("Order", back_populates="items")
    product = db.relationship("Product")


class Payment(db.Model):
    __tablename__ = "payments"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(
        db.Integer, db.ForeignKey("orders.id"), nullable=False, index=True
    )
    provider = db.Column(db.String(24), nullable=False)
    provider_reference = db.Column(db.String(160), unique=True)
    payer_phone_number = db.Column(db.String(32), nullable=False)
    status = db.Column(db.String(32), nullable=False, default="pending", index=True)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)

    order = db.relationship("Order", back_populates="payments")


class Receipt(db.Model):
    __tablename__ = "receipts"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(
        db.Integer, db.ForeignKey("orders.id"), nullable=False, unique=True
    )
    receipt_number = db.Column(db.String(40), nullable=False, unique=True, index=True)
    verification_code = db.Column(
        db.String(64), nullable=False, unique=True, index=True
    )
    issued_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)

    order = db.relationship("Order", back_populates="receipt")


@login_manager.user_loader
def load_admin_user(user_id):
    if user_id.startswith("customer:"):
        customer_id = user_id.split(":", 1)[1]
        if not customer_id.isdecimal():
            return None
        customer = db.session.get(Customer, int(customer_id))
        return customer if customer is not None and customer.password_hash else None
    if user_id.startswith("staff:"):
        staff_id = user_id.split(":", 1)[1]
        if not staff_id.isdecimal():
            return None
        staff = db.session.get(StaffUser, int(staff_id))
        return staff if staff is not None and staff.is_active else None
    if not user_id.isdecimal():
        return None
    admin = db.session.get(AdminUser, int(user_id))
    return admin if admin is not None and admin.is_active else None
