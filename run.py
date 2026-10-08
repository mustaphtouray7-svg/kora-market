from decimal import Decimal

from store import create_app
from store.extensions import db
from flask_migrate import upgrade

app = create_app()

# Render's current service build command installs dependencies only, so apply
# the checked-in Alembic migrations when the production process starts.
with app.app_context():
    upgrade()

    # Add a starter catalog once, so the live market is not empty.
    from store.models import Product

    if Product.query.count() == 0:
        starter_products = [
            ("Rice 5kg", "Quality rice for everyday cooking.", Decimal("200.00")),
            ("Sugar 2kg", "Granulated sugar for home use.", Decimal("400.00")),
            ("Cooking Oil 1L", "Vegetable cooking oil.", Decimal("350.00")),
            ("Milk 1L", "Fresh milk for everyday use.", Decimal("600.00")),
            ("Bread Loaf", "Fresh everyday bread.", Decimal("250.00")),
            ("Onions 1kg", "Fresh onions.", Decimal("450.00")),
            ("Potatoes 2kg", "Fresh potatoes.", Decimal("300.00")),
            ("Chicken 1kg", "Fresh chicken.", Decimal("700.00")),
            ("Fish 1kg", "Fresh market fish.", Decimal("550.00")),
            ("Bottled Water 1.5L", "Clean bottled drinking water.", Decimal("150.00")),
        ]

        for name, description, price in starter_products:
            db.session.add(
                Product(
                    name=name,
                    description=description,
                    price=price,
                    stock_quantity=10,
                    is_active=True,
                )
            )

        db.session.commit()
