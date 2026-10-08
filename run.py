from store import create_app
from flask_migrate import upgrade

app = create_app()

# Render's current service build command installs dependencies only, so apply
# the checked-in Alembic migrations when the production process starts.
with app.app_context():
    upgrade()
