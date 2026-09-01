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
    # ספירת הצי (/admin/fleet-stats). כל "עמוד" כאן הוא GROUP BY של עשרת
    # אלפים דגמים אצל המאגר - כבד ואיטי מעמוד רגיל, ולכן עמוד אחד למנה.
    FLEET_STATS_PAGE_SIZE = int(os.environ.get("FLEET_STATS_PAGE_SIZE", 10000))
    FLEET_STATS_PAGES_PER_CHUNK = int(
        os.environ.get("FLEET_STATS_PAGES_PER_CHUNK", 1)
    )
    # מסלול הסריקה מושך שורות רכב גולמיות. CKAN מגביל ל-32,000 לבקשה,
    # וזה ההבדל בין תשעים בקשות לשלושת אלפים.
    FLEET_STATS_SCAN_PAGE_SIZE = int(
        os.environ.get("FLEET_STATS_SCAN_PAGE_SIZE", 32000)
    )
    FLEET_STATS_TIME_BUDGET = float(os.environ.get("FLEET_STATS_TIME_BUDGET", 25))
    FLEET_STATS_FETCH_ATTEMPTS = int(os.environ.get("FLEET_STATS_FETCH_ATTEMPTS", 3))
    FLEET_STATS_RETRY_PAUSE = float(os.environ.get("FLEET_STATS_RETRY_PAUSE", 2))
    FLEET_STATS_PAGE_PAUSE = float(os.environ.get("FLEET_STATS_PAGE_PAUSE", 0.3))
    FLEET_STATS_MAX_FAILURES = int(os.environ.get("FLEET_STATS_MAX_FAILURES", 3))
    # כמה דגמים נכנסים לדירוג הפערים. פער אצל דגם עם שלושים רכבים אינו
    # הזדמנות, ודירוג עשרות אלפי דגמים אינו בקשת דפדפן.
    FLEET_GAP_MODELS = int(os.environ.get("FLEET_GAP_MODELS", 300))
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
    # שלב 2 במסך הזיהוי - תיאור החלק, צילום ובחירה ידנית. מוסתר כרגע
    # לבקשת המשתמש: הכניסה לחלפים היא דרך רצועת הכיסוי שבכרטיס הרכב.
    # הקוד והמסלול נשארו כמו שהם, ו-SHOW_PART_STEP=1 מחזיר אותו למסך
    # בלי פריסה מחדש.
    SHOW_PART_STEP = os.environ.get("SHOW_PART_STEP", "0").strip() == "1"
    # מרגע שיש Alembic, המיגרציות הן הבעלים היחיד של הסכימה.
    # create_all() לא יודע לשנות טבלה קיימת, ולכן הוא כבוי כברירת מחדל -
    # אחרת שתי מערכות היו מנהלות את אותה סכימה וסותרות זו את זו.
    AUTO_CREATE_TABLES = os.environ.get("AUTO_CREATE_TABLES", "0").strip() == "1"
    # לוג השימוש: שורה לכל בקשה משמעותית. כבוי מייתר את הכתיבה לגמרי,
    # ומספר הימים קובע מה נמחק בהרצת "flask prune-activity".
    ACTIVITY_LOG_ENABLED = os.environ.get(
        "ACTIVITY_LOG_ENABLED", "1"
    ).strip().lower() in {"1", "true", "yes"}
    ACTIVITY_LOG_RETENTION_DAYS = int(
        os.environ.get("ACTIVITY_LOG_RETENTION_DAYS", 90)
    )
    ACTIVITY_PER_PAGE = int(os.environ.get("ACTIVITY_PER_PAGE", 50))
    # אזור הזמן שבו מוצגים הזמנים במסכים. הכתיבה ל-DB תמיד ב-UTC.
    DISPLAY_TIMEZONE = os.environ.get("DISPLAY_TIMEZONE", "Asia/Jerusalem")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}
    PER_PAGE = int(os.environ.get("PER_PAGE", 25))
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB להעלאת קבצים


class TestConfig(Config):
    """הגדרות לבדיקות.

    ברירת המחדל היא SQLite בזיכרון - מהיר, ולא דורש שרת. אבל SQLite
    סלחן במקומות ש-Postgres אינו, ופרודקשן רץ על Postgres: שאילתה
    שעברה כאן ונפלה שם כבר קרתה. לכן אפשר להריץ את אותה חבילת בדיקות
    מול Postgres אמיתי:

        TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:5432/makat_test \
            python -m pytest
    """

    TESTING = True
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "TEST_DATABASE_URL", "sqlite:///:memory:"
    )
    SUPERADMIN_EMAILS = frozenset()
    VEHICLE_IMPORT_RETRY_PAUSE = 0  # בלי המתנות אמיתיות בבדיקות
    VEHICLE_IMPORT_PAGE_PAUSE = 0
    FLEET_STATS_RETRY_PAUSE = 0
    FLEET_STATS_PAGE_PAUSE = 0
    AUTO_CREATE_TABLES = True
    IS_MANAGED_PLATFORM = False
    READ_ONLY = False
    CSRF_ENABLED = False
    WTF_CSRF_ENABLED = False
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_SECURE = False
