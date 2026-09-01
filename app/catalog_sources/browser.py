"""דפדפן אמיתי, כי קטלוג אמיתי לא מגיש HTML סטטי.

Laximo ו-TecDoc בונים את התוצאה ב-JavaScript אחרי טעינת הדף, ולעיתים
מאחורי הזדהות. ``urllib`` מקבל מהם שלד ריק. לכן השליפה מהאתרים האלה
עוברת ב-Chromium דרך Playwright: אותו דף שאדם היה רואה.

שלושה דברים שהמודול הזה קיים בשבילם:

1. **איתור הדפדפן.** התקנת Playwright מגיעה עם מספר build משלה, והתמונה
   שמריצה אותנו עשויה להחזיק build אחר. ``pw.chromium.launch()`` נכשל אז
   על נתיב שלא קיים. כאן מאתרים את מה שבאמת מותקן ומעבירים אותו במפורש.

2. **דפדפן אחד לתהליך.** הפעלת Chromium לוקחת שנייה ומאתיים מגה. עבודת
   שליפה שמריצה שני מקורות לא תשלם על זה פעמיים, ושני workers של
   gunicorn לא ידרכו זה על זה - לכל אחד יש את שלו, מאחורי מנעול.

3. **מצב מזוהה.** קטלוג בתשלום נפתח אחרי כניסה. ``CATALOG_STORAGE_STATE``
   מצביע על קובץ עוגיות שנשמר פעם אחת, וכל שליפה מכאן והלאה נכנסת איתו.

**למה thread ייעודי ולא מנעול.** ה-API הסינכרוני של Playwright קשור
לthread שיצר אותו: אובייקט שנוצר ב-thread אחד אינו חוקי באחר. gunicorn
כאן רץ ב-``gthread`` (ראה ``gunicorn.conf.py``), כלומר בקשות מגיעות
מ-threads שונים, ומנעול בלבד היה נותן דפדפן שנוצר בבקשה הראשונה
ונשבר בשנייה. לכן הדפדפן חי ב-thread אחד קבוע, וכל שליפה נשלחת אליו
ומחכה לתשובה. זה גם מסדר את הבקשות בטור - מה שממילא רצוי מול אתר
של מישהו אחר.
"""
import atexit
import glob
import os
import threading
from concurrent.futures import ThreadPoolExecutor

# הדפדפן כבד. כיבוי מפורש משאיר את שאר המערכת עובדת בלעדיו.
BROWSER_ENABLED = os.environ.get("CATALOG_BROWSER", "1").strip() != "0"
# תקציב הזמן לדף אחד. חייב להישאר בבטחה מתחת ל-WEB_TIMEOUT של gunicorn
# (60 שניות), כי בקשת שלב אחת = טעינת דף אחת ועוד קריאה למודל.
BROWSER_TIMEOUT = float(os.environ.get("CATALOG_BROWSER_TIMEOUT", 20))
# מתי הדף נחשב טעון. networkidle מתאים לקטלוג שממשיך למשוך נתונים
# אחרי ההצגה הראשונה, וזה בדיוק המקרה כאן.
WAIT_UNTIL = os.environ.get("CATALOG_BROWSER_WAIT", "networkidle")
STORAGE_STATE = os.environ.get("CATALOG_STORAGE_STATE", "").strip()
LOCALE = os.environ.get("CATALOG_BROWSER_LOCALE", "he-IL")

_lock = threading.Lock()
_worker = None          # ה-thread היחיד שמחזיק את הדפדפן
_playwright = None
_browser = None


def chromium_path():
    """הנתיב ל-Chromium שבאמת מותקן, או None אם נשאיר ל-Playwright לבחור."""
    explicit = os.environ.get("PLAYWRIGHT_CHROMIUM_PATH", "").strip()
    if explicit:
        return explicit if os.path.exists(explicit) else None
    root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    if not root:
        return None
    for pattern in ("chromium-*/chrome-linux/chrome",
                    "chromium_headless_shell-*/chrome-linux/headless_shell"):
        found = sorted(glob.glob(os.path.join(root, pattern)))
        if found:
            return found[-1]
    return None


def browser_available():
    """האם יש Playwright מותקן ודפדפן להריץ."""
    if not BROWSER_ENABLED:
        return False
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    return True


def _worker_thread():
    """ה-thread שבבעלותו הדפדפן. נוצר פעם אחת לתהליך."""
    global _worker
    with _lock:
        if _worker is None:
            _worker = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="catalog-browser"
            )
            atexit.register(shutdown)
    return _worker


def _launch():
    """מפעיל דפדפן אחד. רץ *רק* בתוך ה-thread הייעודי."""
    global _playwright, _browser
    if _browser is not None and _browser.is_connected():
        return _browser
    from playwright.sync_api import sync_playwright

    _playwright = sync_playwright().start()
    args = ["--no-sandbox", "--disable-dev-shm-usage"]
    executable = chromium_path()
    _browser = _playwright.chromium.launch(
        headless=True, args=args, **({"executable_path": executable} if executable else {})
    )
    return _browser


def _teardown():
    """סגירה בתוך ה-thread הייעודי - שם האובייקטים חוקיים."""
    global _playwright, _browser
    if _browser is not None:
        try:
            _browser.close()
        except Exception:
            pass
        _browser = None
    if _playwright is not None:
        try:
            _playwright.stop()
        except Exception:
            pass
        _playwright = None


def shutdown():
    """סוגר את הדפדפן ואת ה-thread שלו. נקרא בכיבוי, ובבדיקות."""
    global _worker
    with _lock:
        worker, _worker = _worker, None
    if worker is not None:
        try:
            worker.submit(_teardown).result(timeout=30)
        except Exception:
            pass
        worker.shutdown(wait=False)


class BrowserError(Exception):
    """הדף לא נטען. סיבה קריאה לאדם."""


def fetch_page(url, wait_selector=None, timeout=None, user_agent=None):
    """פותח את הכתובת בדפדפן ומחזיר את ה-HTML אחרי שהוא נבנה.

    ``wait_selector`` הוא מה שמבדיל בין "הדף ענה" לבין "התוצאה כאן":
    קטלוג מציג שלד ואז ממלא אותו, ובלי המתנה לאלמנט אמיתי היינו קוראים
    את השלד.
    """
    if not browser_available():
        raise BrowserError(
            "Playwright אינו מותקן או שהדפדפן כבוי (CATALOG_BROWSER=0). "
            "התקנה: pip install -r requirements-browser.txt && playwright install chromium"
        )
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    limit = (timeout or BROWSER_TIMEOUT) * 1000
    context_args = {"locale": LOCALE}
    if user_agent:
        context_args["user_agent"] = user_agent
    if STORAGE_STATE and os.path.exists(STORAGE_STATE):
        context_args["storage_state"] = STORAGE_STATE

    def work():
        browser = _launch()
        context = browser.new_context(**context_args)
        try:
            page = context.new_page()
            page.set_default_timeout(limit)
            try:
                page.goto(url, wait_until=WAIT_UNTIL, timeout=limit)
                if wait_selector:
                    # התוצאה עשויה שלא להופיע כלל (אין חלק כזה לרכב).
                    # זה לא כשל - נחזיר את הדף כמו שהוא ונשאיר למודל לומר.
                    try:
                        page.wait_for_selector(wait_selector, timeout=limit / 2)
                    except PlaywrightTimeout:
                        pass
                return page.content()
            except PlaywrightTimeout as exc:
                raise BrowserError(f"פסק זמן בטעינת {url}") from exc
            except PlaywrightError as exc:
                raise BrowserError(f"{url}: {str(exc).splitlines()[0]}") from exc
        finally:
            context.close()

    # שוליים על התקציב של הדף: המתנה בתור אינה כשל של הדף עצמו.
    return _worker_thread().submit(work).result(timeout=(limit / 1000) + 30)


class BrowserFetcher:
    """מתאם לחתימת ה-fetcher של המקורות: ``fetcher(url, timeout=None)``."""

    def __init__(self, wait_selector=None, user_agent=None):
        self.wait_selector = wait_selector
        self.user_agent = user_agent

    def __call__(self, url, timeout=None):
        return fetch_page(
            url,
            wait_selector=self.wait_selector,
            timeout=timeout,
            user_agent=self.user_agent,
        )
