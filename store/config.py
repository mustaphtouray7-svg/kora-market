import os


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY")
    APP_ENV = os.environ.get("APP_ENV", "development").lower()
    DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///store.db")
    # Render and several managed providers may supply legacy postgres:// URLs.
    # Use psycopg 3 explicitly so PostgreSQL works with the installed driver.
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
    elif DATABASE_URL.startswith("postgresql://"):
        DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)
    SQLALCHEMY_DATABASE_URI = DATABASE_URL
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = APP_ENV == "production"
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SECURE = APP_ENV == "production"
