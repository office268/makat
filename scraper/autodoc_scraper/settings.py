"""הגדרות הגריד.

כל מה שעשוי להשתנות מול האתר החי - קצב, זהות הדפדפן, כיבוד robots -
נקרא ממשתני סביבה, כדי שמנהל האפליקציה יוכל לכוונן בלי לגעת בקוד
ובלי פריסה מחדש.

ברירת המחדל היא איטית ומנומסת בכוונה: בקשה אחת בכל פעם, שנייה וחצי
המתנה ביניהן וכיבוד robots.txt. גריד מהיר מול חנות מקוונת נחסם, וגם
צריך להיחסם.
"""
import os


def _flag(name, default="1"):
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes"}


BOT_NAME = "autodoc_scraper"
SPIDER_MODULES = ["autodoc_scraper.spiders"]
NEWSPIDER_MODULE = "autodoc_scraper.spiders"

# כיבוד robots.txt דלוק כברירת מחדל. הכיבוי קיים למי שיש לו הסכם מול
# האתר - זו החלטה של מי שמפעיל, ולא ברירת מחדל שנופלת עליו בשקט.
ROBOTSTXT_OBEY = _flag("AUTODOC_OBEY_ROBOTS")

USER_AGENT = os.environ.get(
    "AUTODOC_USER_AGENT",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36",
)
DEFAULT_REQUEST_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": os.environ.get("AUTODOC_ACCEPT_LANGUAGE", "he,en;q=0.8"),
}

CONCURRENT_REQUESTS = 1
CONCURRENT_REQUESTS_PER_DOMAIN = 1
DOWNLOAD_DELAY = float(os.environ.get("AUTODOC_DOWNLOAD_DELAY", 1.5))
AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = DOWNLOAD_DELAY
AUTOTHROTTLE_MAX_DELAY = 15.0
AUTOTHROTTLE_TARGET_CONCURRENCY = 1.0

DOWNLOAD_TIMEOUT = float(os.environ.get("AUTODOC_DOWNLOAD_TIMEOUT", 20))
RETRY_ENABLED = True
RETRY_TIMES = int(os.environ.get("AUTODOC_RETRY_TIMES", 2))

# תקרה קשיחה על מה שהרצה אחת מביאה. המסך מריץ מטרה אחת בכל בקשת HTTP,
# ו-gunicorn הורג בקשה אחרי 60 שניות: עמוד ראשון של תוצאות מספיק.
CLOSESPIDER_ITEMCOUNT = int(os.environ.get("AUTODOC_MAX_ITEMS", 30))
CLOSESPIDER_TIMEOUT = float(os.environ.get("AUTODOC_SPIDER_TIMEOUT", 35))

ITEM_PIPELINES = {"autodoc_scraper.pipelines.CleanPartPipeline": 300}

FEED_EXPORT_ENCODING = "utf-8"
# ברירת המחדל של Scrapy מציפה את ה-stderr בשורות INFO. התהליך שמפעיל
# אותנו קורא את ה-stderr רק כדי לדווח כשלים, ולכן רק שגיאות נכתבות.
LOG_LEVEL = os.environ.get("AUTODOC_LOG_LEVEL", "ERROR")
TELNETCONSOLE_ENABLED = False
REQUEST_FINGERPRINTER_IMPLEMENTATION = "2.7"
TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"
