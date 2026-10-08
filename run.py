from decimal import Decimal

from store import create_app
from store.extensions import db
from flask_migrate import upgrade

app = create_app()

# Render's current service build command installs dependencies only, so apply
# the checked-in Alembic migrations when the production process starts.
with app.app_context():
    upgrade()

    from store.models import Branch, Product

    starter_branches = [
        "Latri Kunda",
        "Serekunda",
        "Banjul",
        "Brikama",
    ]

    existing_branches = {branch.name for branch in Branch.query.all()}
    new_branches = [
        Branch(name=name, is_active=True)
        for name in starter_branches
        if name not in existing_branches
    ]
    if new_branches:
        db.session.add_all(new_branches)
        db.session.commit()

    starter_products = [
        ("Rice 5kg", "Quality rice for everyday cooking.", Decimal("200.00"), "https://images.unsplash.com/photo-1586201375761-83865001e31c?auto=format&fit=crop&w=900&q=80"),
        ("Sugar 2kg", "Granulated sugar for home use.", Decimal("400.00"), "https://images.unsplash.com/photo-1518013431117-eb1465fa5752?auto=format&fit=crop&w=900&q=80"),
        ("Cooking Oil 1L", "Vegetable cooking oil.", Decimal("350.00"), "https://images.unsplash.com/photo-1474979266404-7eaacbcd87c5?auto=format&fit=crop&w=900&q=80"),
        ("Milk 1L", "Fresh milk for everyday use.", Decimal("600.00"), "https://images.unsplash.com/photo-1550583724-b2692b85b150?auto=format&fit=crop&w=900&q=80"),
        ("Bread Loaf", "Fresh everyday bread.", Decimal("250.00"), "https://images.unsplash.com/photo-1509440159596-0249088772ff?auto=format&fit=crop&w=900&q=80"),
        ("Onions 1kg", "Fresh onions.", Decimal("450.00"), "https://images.unsplash.com/photo-1518977676601-b53f82aba655?auto=format&fit=crop&w=900&q=80"),
        ("Potatoes 2kg", "Fresh potatoes.", Decimal("300.00"), "https://images.unsplash.com/photo-1518977676601-b53f82aba655?auto=format&fit=crop&w=900&q=80"),
        ("Chicken 1kg", "Fresh chicken.", Decimal("700.00"), "https://images.unsplash.com/photo-1604503468506-a8da13d82791?auto=format&fit=crop&w=900&q=80"),
        ("Fish 1kg", "Fresh market fish.", Decimal("550.00"), "https://images.unsplash.com/photo-1534766438357-2b270b99f2a7?auto=format&fit=crop&w=900&q=80"),
        ("Bottled Water 1.5L", "Clean bottled drinking water.", Decimal("150.00"), "https://images.unsplash.com/photo-1564419320461-6870880221ad?auto=format&fit=crop&w=900&q=80"),
    ]

    existing = {p.name: p for p in Product.query.all()}

    if not existing:
        for name, description, price, image_url in starter_products:
            db.session.add(
                Product(
                    name=name,
                    description=description,
                    price=price,
                    image_url=image_url,
                    stock_quantity=10,
                    is_active=True,
                )
            )
        db.session.commit()
    else:
        # The first catalog was created without images. Fill them in without
        # changing any products the admin may already have edited.
        changed = False
        for name, _, _, image_url in starter_products:
            product = existing.get(name)
            if product and not product.image_url:
                product.image_url = image_url
                changed = True
        if changed:
            db.session.commit()
