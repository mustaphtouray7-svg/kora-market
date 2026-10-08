# Kora Market

A Flask-based foundation for a professional, mobile-friendly business store.
The current build establishes the application, database, staff authentication,
and core relational schema. Store browsing, checkout, receipts, and payment
provider integrations are intentionally future phases.

## Run locally

Requires Python 3.10 or later.

```powershell
# If `py` is not on PATH, replace it with the full path to your Python executable.
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:SECRET_KEY = (& python -c "import secrets; print(secrets.token_hex(32))")
flask --app run.py db upgrade
flask --app run.py create-admin
flask --app run.py run
```

Open `http://127.0.0.1:5000`. The default database is SQLite in Flask's
`instance` directory. The checked-in initial migration creates its schema when
you run `flask --app run.py db upgrade`. To use PostgreSQL later, install a
PostgreSQL DBAPI driver and set `DATABASE_URL` to a SQLAlchemy PostgreSQL URL;
the ORM models use portable SQLAlchemy types.

Set a stable, randomly generated `SECRET_KEY` in every environment. Set
`APP_ENV=production` in production; startup refuses to use an unset secret key
and session cookies are then marked secure. Run database migrations during
deployment rather than creating or changing production tables at app startup.

## Security and payment scope

- Admin passwords are hashed with Werkzeug and are never stored in plaintext.
- Authentication forms use global Flask-WTF CSRF protection.
- Admin pages require an authenticated account; sign-out uses a CSRF-protected
  POST request.
- Do not put wallet PINs or passwords in this application.
- Payment rows are only schema groundwork. No payment is created, confirmed,
  or marked paid by this build. Payment processing must use official provider
  authorization and verified server-side provider notifications.
- Never use the development server or development configuration in production.
