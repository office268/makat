"""הגדרות האפליקציה."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


class Config:
    """הגדרות בסיס."""

    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", f"sqlite:///{BASE_DIR / 'instance' / 'makat.db'}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    JSON_AS_ASCII = False
    PER_PAGE = int(os.environ.get("PER_PAGE", 25))
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB להעלאת קבצי CSV


class TestConfig(Config):
    """הגדרות לבדיקות."""

    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False
