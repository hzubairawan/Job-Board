"""Application configuration. Secrets come from environment variables only."""
import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL") or (
        "sqlite:///" + str(BASE_DIR / "instance" / "app.db")
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER") or str(BASE_DIR / "uploads")
    MAX_RESUME_BYTES = 5 * 1024 * 1024  # 5 MB per resume
    MAX_CONTENT_LENGTH = 6 * 1024 * 1024  # request cap (resume + form overhead)

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = False
    PERMANENT_SESSION_LIFETIME = timedelta(hours=4)

    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5").strip()
    AI_CALLS_PER_HOUR = int(os.environ.get("AI_CALLS_PER_HOUR", "30"))

    CSRF_ENABLED = True
    PER_PAGE = 9
    ADMIN_PER_PAGE = 12


class DevelopmentConfig(Config):
    DEBUG = False  # run with `flask --debug run` when you want the debugger


class ProductionConfig(Config):
    SESSION_COOKIE_SECURE = True
    PREFERRED_URL_SCHEME = "https"


class TestingConfig(Config):
    TESTING = True
    SECRET_KEY = "test-secret-key"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SESSION_COOKIE_SECURE = False


CONFIGS = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}


def get_config():
    return CONFIGS.get(os.environ.get("APP_ENV", "development"), DevelopmentConfig)
