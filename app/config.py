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


def _superadmin_emails():
    """כתובות בעלות הרשאת-על, ממשתנה הסביבה SUPERADMIN_EMAILS.

    ההרשאה הזו חוצה ארגונים ולכן היא נשלטת מהסביבה בלבד - אין דרך להעניק
    אותה מתוך היישום, וכל מי שיכול לשנות אותה כבר שולט בשרת ממילא.
    """
    raw = os.environ.get("SUPERADMIN_EMAILS", "")
    return frozenset(part.strip().lower() for part in raw.split(",") if part.strip())


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
    SUPERADMIN_EMAILS = _superadmin_emails()
    # כמה עמודים (1000 רשומות כל אחד) לייבא בבקשת HTTP אחת, ותקציב הזמן
    # שלה. gunicorn הורג בקשה אחרי 60 שניות, ולכן המנה חייבת לעצור הרבה לפניו.
    VEHICLE_IMPORT_PAGES_PER_CHUNK = int(
        os.environ.get("VEHICLE_IMPORT_PAGES_PER_CHUNK", 3)
    )
    VEHICLE_IMPORT_TIME_BUDGET = float(
        os.environ.get("VEHICLE_IMPORT_TIME_BUDGET", 25)
    )
    # המאגר הממשלתי מחזיר שגיאה זמנית תחת רצף בקשות ארוך. מנסים שוב
    # את אותו עמוד, נושמים בין עמודים, ומוותרים אחרי כמה מנות רצופות
    # שלא התקדמו - אחרת הדפדפן מנסה שוב לנצח.
    VEHICLE_IMPORT_FETCH_ATTEMPTS = int(
        os.environ.get("VEHICLE_IMPORT_FETCH_ATTEMPTS", 3)
    )
    VEHICLE_IMPORT_RETRY_PAUSE = float(
        os.environ.get("VEHICLE_IMPORT_RETRY_PAUSE", 2)
    )
    VEHICLE_IMPORT_PAGE_PAUSE = float(
        os.environ.get("VEHICLE_IMPORT_PAGE_PAUSE", 0.3)
    )
    VEHICLE_IMPORT_MAX_FAILURES = int(
        os.environ.get("VEHICLE_IMPORT_MAX_FAILURES", 3)
    )
    # נעילת כתיבה עד שמנגנון ההרשאות ייכנס. פתוח כברירת מחדל בפיתוח
    # מקומי, נעול כברירת מחדל בפרודקשן.
    # מזהה גרסת ה-service worker. שינוי שלו גורם לדפדפנים למשוך
    # מחדש את הנכסים במטמון. מוגדר אוטומטית מהקומיט בפריסה.
    SW_VERSION = os.environ.get("RAILWAY_GIT_COMMIT_SHA", "dev")[:12] or "dev"

    CSRF_ENABLED = True
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    # הקוקי נשלח רק על HTTPS כשאנחנו על פלטפורמה מנוהלת
    SESSION_COOKIE_SECURE = _is_managed_platform()
    REMEMBER_COOKIE_SECURE = _is_managed_platform()

    READ_ONLY = os.environ.get(
        "READ_ONLY", "1" if _is_managed_platform() else "0"
    ).strip().lower() in {"1", "true", "yes"}
    # מרגע שיש Alembic, המיגרציות הן הבעלים היחיד של הסכימה.
    # create_all() לא יודע לשנות טבלה קיימת, ולכן הוא כבוי כברירת מחדל -
    # אחרת שתי מערכות היו מנהלות את אותה סכימה וסותרות זו את זו.
    AUTO_CREATE_TABLES = os.environ.get("AUTO_CREATE_TABLES", "0").strip() == "1"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}
    JSON_AS_ASCII = False
    PER_PAGE = int(os.environ.get("PER_PAGE", 25))
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB להעלאת קבצים


class TestConfig(Config):
    """הגדרות לבדיקות."""

    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SUPERADMIN_EMAILS = frozenset()
    VEHICLE_IMPORT_RETRY_PAUSE = 0  # בלי המתנות אמיתיות בבדיקות
    VEHICLE_IMPORT_PAGE_PAUSE = 0
    AUTO_CREATE_TABLES = True
    IS_MANAGED_PLATFORM = False
    READ_ONLY = False
    CSRF_ENABLED = False
    WTF_CSRF_ENABLED = False
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_SECURE = False
