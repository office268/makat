"""הגדרות האפליקציה."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _database_uri():
    """בונה את כתובת בסיס הנתונים.

    סדר העדיפויות:
      1. DATABASE_URL - מוזרק אוטומטית ע"י Railway כשמחברים שירות Postgres.
      2. DATA_DIR     - קובץ SQLite על ווליום קבוע (חלופה לפרודקשן בלי Postgres).
      3. instance/    - SQLite מקומי לפיתוח.

    שים לב: SQLite על הדיסק הרגיל של Railway נמחק בכל deploy, כי כל דיפלוי
    מקבל מערכת קבצים נקייה. לכן פרודקשן = Postgres, או ווליום ממופה ל-DATA_DIR.
    """
    url = os.environ.get("DATABASE_URL")
    if url:
        # SQLAlchemy 2 דורש את הסכמה המפורשת; Railway מספק את הקצרה
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+psycopg://", 1)
        elif url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        return url

    data_dir = os.environ.get("DATA_DIR")
    if data_dir:
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{Path(data_dir) / 'makat.db'}"

    return f"sqlite:///{BASE_DIR / 'instance' / 'makat.db'}"


def _is_managed_platform():
    """האם אנחנו רצים על פלטפורמה מנוהלת (Railway) ולא על מחשב מקומי."""
    return bool(
        os.environ.get("RAILWAY_ENVIRONMENT_NAME")
        or os.environ.get("RAILWAY_SERVICE_ID")
        or os.environ.get("RAILWAY_PROJECT_ID")
    )


class Config:
    """הגדרות בסיס."""

    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")
    SQLALCHEMY_DATABASE_URI = _database_uri()
    IS_MANAGED_PLATFORM = _is_managed_platform()
    # יצירת טבלאות בעליית האפליקציה מתאימה ל-SQLite מקומי בלבד.
    # מול Postgres זה רץ פעם אחת ב-preDeployCommand.
    AUTO_CREATE_TABLES = SQLALCHEMY_DATABASE_URI.startswith("sqlite")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}
    JSON_AS_ASCII = False
    PER_PAGE = int(os.environ.get("PER_PAGE", 25))
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB להעלאת קבצים


class TestConfig(Config):
    """הגדרות לבדיקות."""

    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    AUTO_CREATE_TABLES = True
    IS_MANAGED_PLATFORM = False
    WTF_CSRF_ENABLED = False
