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

4. **חיפוש שאינו כתובת.** לא בכל קטלוג אפשר להגיע לתוצאה בכתובת עם
   פרמטר. איפה שצריך למלא שדה וללחוץ - ``fill_selector`` ו-
   ``submit_selector`` עושים בדיוק את זה, כמו אדם.

**אורח, לא בוט.** ``robots.txt`` נבדק גם כאן ולא רק בהבאה הפשוטה,
העוגיות של הודעת ההסכמה נסגרות כדי שהדף ייבנה, וה-User-Agent הוא של
דפדפן אמיתי - כי דף שמוגש לבוט אינו הדף שאנחנו צריכים לקרוא. אין כאן
עקיפה של הזדהות או של חסימה: מה שדורש חשבון ימשיך לדרוש חשבון.

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
# דף שמוגש ל-HeadlessChrome אינו הדף שמוגש לאדם, ואנחנו צריכים את השני.
BROWSER_UA = os.environ.get(
    "CATALOG_BROWSER_UA",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36",
)
# הודעת ההסכמה חוסמת את התוכן בכל אתר אירופי. סוגרים אותה כמו שאדם סוגר.
CONSENT_SELECTORS = [
    part.strip()
    for part in os.environ.get(
        "CATALOG_CONSENT_SELECTORS",
        "#onetrust-accept-btn-handler,"
        "button#didomi-notice-agree-button,"
        ".cookie-accept,"
        "button[aria-label='Accept all']",
    ).split(",")
    if part.strip()
]

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


INSTALL_HINT = (
    "הדפדפן לא מותקן. בסביבה מקומית: playwright install chromium. "
    "בפריסה: playwright install --with-deps chromium בשלב הבנייה."
)


def browser_available():
    """האם יש Playwright *וגם* דפדפן להריץ.

    הספרייה לבדה אינה מספיקה: ``pip install playwright`` לא מוריד את
    Chromium. בלי הבדיקה הזו הפיצ'ר היה נראה זמין ונופל רק כשמישהו
    לוחץ, וזה בדיוק הכשל שאין ממנו דרך חזרה על מסך של מכונאי.
    """
    if not BROWSER_ENABLED:
        return False
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        return False
    return chromium_installed()


def chromium_installed():
    """האם יש קובץ דפדפן על הדיסק - זה שאיתרנו, או זה של Playwright."""
    found = chromium_path()
    if found:
        return os.path.exists(found)
    # בלי PLAYWRIGHT_BROWSERS_PATH, Playwright מוריד לתיקיית המטמון שלו
    for root in (
        os.path.expanduser("~/.cache/ms-playwright"),
        "/root/.cache/ms-playwright",
    ):
        if glob.glob(os.path.join(root, "chromium*/chrome-linux/chrome")):
            return True
    return False


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
    args = [
        "--no-sandbox",
        "--disable-dev-shm-usage",
        # בלי זה navigator.webdriver=true, ואתרים מגישים דף אחר לגמרי
        "--disable-blink-features=AutomationControlled",
    ]
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


def _dismiss_consent(page):
    """סוגר את הודעת העוגיות אם היא שם. היעדרה אינו כשל."""
    for selector in CONSENT_SELECTORS:
        try:
            button = page.query_selector(selector)
            if button and button.is_visible():
                button.click(timeout=2000)
                page.wait_for_timeout(300)
                return True
        except Exception:
            continue
    return False


def fetch_page(url, wait_selector=None, timeout=None, user_agent=None,
               fill_selector=None, fill_value=None, submit_selector=None):
    """פותח את הכתובת בדפדפן ומחזיר את ה-HTML אחרי שהוא נבנה.

    ``wait_selector`` הוא מה שמבדיל בין "הדף ענה" לבין "התוצאה כאן":
    קטלוג מציג שלד ואז ממלא אותו, ובלי המתנה לאלמנט אמיתי היינו קוראים
    את השלד.

    ``fill_selector``/``fill_value`` הם למקרה שהחיפוש אינו כתובת אלא
    טופס: ממלאים את השדה, לוחצים על ``submit_selector`` (או Enter),
    ומחכים שהתוצאה תיבנה.
    """
    if not BROWSER_ENABLED:
        raise BrowserError("מסלול הדפדפן כבוי (CATALOG_BROWSER=0).")
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError as exc:
        raise BrowserError(
            "Playwright אינו מותקן. pip install -r requirements.txt"
        ) from exc
    if not chromium_installed():
        raise BrowserError(INSTALL_HINT)
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    from .base import allowed_by_robots

    if not allowed_by_robots(url, agent=user_agent or BROWSER_UA):
        raise BrowserError(f"robots.txt של האתר אוסר את הכתובת: {url}")

    limit = (timeout or BROWSER_TIMEOUT) * 1000
    context_args = {"locale": LOCALE, "user_agent": user_agent or BROWSER_UA}
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
                _dismiss_consent(page)
                if fill_selector and fill_value:
                    page.fill(fill_selector, fill_value, timeout=limit / 2)
                    if submit_selector:
                        page.click(submit_selector, timeout=limit / 2)
                    else:
                        page.press(fill_selector, "Enter")
                    page.wait_for_load_state(WAIT_UNTIL, timeout=limit)
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

    def __init__(self, wait_selector=None, user_agent=None,
                 fill_selector=None, fill_value=None, submit_selector=None):
        self.wait_selector = wait_selector
        self.user_agent = user_agent
        self.fill_selector = fill_selector
        self.fill_value = fill_value
        self.submit_selector = submit_selector

    def __call__(self, url, timeout=None):
        return fetch_page(
            url,
            wait_selector=self.wait_selector,
            timeout=timeout,
            user_agent=self.user_agent,
            fill_selector=self.fill_selector,
            fill_value=self.fill_value,
            submit_selector=self.submit_selector,
        )
