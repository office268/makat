"""התשתית המשותפת לכל מקור: הבאה מהרשת, צמצום הדף, ופענוח במודל.

למה שליפה דטרמיניסטית + פענוח במודל, ולא סלקטורים:

  * סלקטורים נשברים בכל שינוי עיצוב באתר, ובשקט.
  * גרידה תמימה מכניסה חלקים של רכב אחר. זה לא תרחיש תיאורטי - בעמוד
    הקורולה נמצא מסנן שהתיאור שלו אומר CHERY AMULET (ראה
    ``parts_discovery``).

לכן הקוד עושה את מה שקוד עושה טוב - בונה כתובת מהשלדה, מביא, מצמצם,
מגביל קצב - והמודל עושה את מה שהוא עושה טוב: קורא דף ומחזיר ממנו את
החלק שבאמת שייך לרכב הזה. שלב ההבאה ניתן לבדיקה בלי רשת (fixtures),
ושלב הפענוח ניתן לבדיקה בלי מפתח (מודל מוזרק).
"""
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass, field, asdict
from html.parser import HTMLParser

USER_AGENT = os.environ.get(
    "CATALOG_USER_AGENT", "makat/1.0 (+https://github.com/office268/makat)"
)
FETCH_TIMEOUT = float(os.environ.get("CATALOG_FETCH_TIMEOUT", 12))
# כמה תווים מהדף המצומצם נשלחים למודל. דף קטלוג מגיע ל-300KB, והחלק
# המעניין בו הוא כמה אלפי תווים.
MAX_CONDENSED = int(os.environ.get("CATALOG_MAX_CONDENSED", 30000))
# נשימה בין בקשות לאותו מארח. אנחנו אורחים באתר של מישהו אחר.
HOST_PAUSE = float(os.environ.get("CATALOG_HOST_PAUSE", 1.0))
RESPECT_ROBOTS = os.environ.get("CATALOG_RESPECT_ROBOTS", "1").strip() != "0"

PARSE_MODEL = os.environ.get("CATALOG_PARSE_MODEL", "claude-opus-5")

_last_fetch = {}
_robots = {}


@dataclass
class Candidate:
    """מק"ט אחד שמקור החזיר, לפני אימות ולפני שמירה.

    ``tier`` הוא מה שקובע את הסימון על המסך: ``oem`` הוא התאמה לשלדה
    הזו, ``aftermarket`` הוא חלופי שמקושר למק"ט המקורי. ``confidence``
    נשאר טקסטואלי ("high"/"low") כדי שיתאים לאימות שכבר קיים בגילוי.
    """

    part_number: str
    manufacturer: str = ""
    tier: str = "aftermarket"
    oe_number: str = ""
    oe_brand: str = ""
    image_url: str = ""
    price_eur: float = None
    source_url: str = ""
    source_key: str = ""
    variant_key: str = ""
    confidence: str = "low"
    note: str = ""
    extra: dict = field(default_factory=dict)

    def as_row(self):
        """המבנה ש-``parts_discovery.validate`` יודע לקרוא."""
        return asdict(self)


class CatalogSource:
    """הממשק. מקור חדש = מחלקה שיורשת ומממשת ``lookup``."""

    key = ""
    name = ""
    tier = "aftermarket"
    # האם המקור זקוק למספר שלדה (ולא רק ליצרן/דגם)
    needs_vin = False
    # האם האתר בונה את התוצאה ב-JavaScript. אם כן, הבאה פשוטה תחזיר
    # שלד ריק, והמקור לא ייחשב זמין בלי דפדפן או ScraperAPI.
    needs_js = False

    def available(self):
        """האם אפשר להריץ אותו עכשיו - הגדרות, מפתח, כתובת."""
        return True

    def lookup(self, vehicle, part_type, oem_numbers=(), fetcher=None, client=None):
        """מחזיר רשימת ``Candidate``. לא מרים חריגה על "לא נמצא"."""
        raise NotImplementedError

    def __repr__(self):  # pragma: no cover - נוחות דיבוג
        return f"<{type(self).__name__} {self.key}>"


# --------------------------------------------------------------------------
# הבאה מהרשת
# --------------------------------------------------------------------------

class FetchError(Exception):
    """הדף לא הגיע. סיבה קריאה לאדם, לא traceback."""


def _robots_for(url):
    parts = urllib.parse.urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    if origin in _robots:
        return _robots[origin]
    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(f"{origin}/robots.txt")
    try:
        parser.read()
    except Exception:
        # אתר בלי robots.txt נגיש אינו אתר שאוסר. מסמנים None כדי
        # שהבדיקה תדלג, ולא נחסום את עצמנו בגלל שגיאת רשת רגעית.
        parser = None
    _robots[origin] = parser
    return parser


def allowed_by_robots(url, agent=USER_AGENT):
    """האם ה-robots.txt של האתר מתיר לנו את הכתובת הזו."""
    if not RESPECT_ROBOTS:
        return True
    parser = _robots_for(url)
    if parser is None:
        return True
    return parser.can_fetch(agent, url)


def _breathe(url):
    """נשימה בין בקשות לאותו מארח."""
    host = urllib.parse.urlsplit(url).netloc
    last = _last_fetch.get(host)
    if last is not None:
        wait = HOST_PAUSE - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)
    _last_fetch[host] = time.monotonic()


def fetch(url, timeout=None):
    """מביא דף. מרים ``FetchError`` עם סיבה קריאה, לא מחזיר None שקט."""
    if not allowed_by_robots(url):
        raise FetchError(f"robots.txt של האתר אוסר את הכתובת: {url}")
    _breathe(url)
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "he,en;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout or FETCH_TIMEOUT) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")
    except urllib.error.HTTPError as exc:
        raise FetchError(f"האתר החזיר {exc.code} עבור {url}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise FetchError(f"לא ניתן להגיע ל-{url}: {exc}") from exc


# --------------------------------------------------------------------------
# צמצום הדף
# --------------------------------------------------------------------------

_SKIP_TAGS = {"script", "style", "noscript", "svg", "head"}


class _Condenser(HTMLParser):
    """מוציא מהדף טקסט, תמונות וקישורים - ומשליך את השאר.

    התמונות נשמרות בשורה משלהן ובפורמט קבוע, כי הן חצי מהתשובה שאנחנו
    מחפשים והמודל צריך למצוא אותן בלי לנחש.
    """

    def __init__(self, base_url=""):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.chunks = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1
            return
        values = dict(attrs)
        if tag == "img":
            src = values.get("data-src") or values.get("src") or ""
            if src and not src.startswith("data:"):
                alt = (values.get("alt") or "").strip()
                self.chunks.append(f"[IMG {urllib.parse.urljoin(self.base_url, src)} | {alt}]")
        elif tag == "a":
            href = values.get("href") or ""
            if href and not href.startswith(("#", "javascript:")):
                self.chunks.append(f"[LINK {urllib.parse.urljoin(self.base_url, href)}]")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if self._skip:
            return
        text = " ".join(data.split())
        if text:
            self.chunks.append(text)


def condense(html, base_url="", limit=None):
    """דף HTML -> טקסט קצר שאפשר לשלוח למודל."""
    parser = _Condenser(base_url)
    try:
        parser.feed(html or "")
    except Exception:
        # HTML שבור הוא נורמלי באתרים אמיתיים. מה שנאסף עד הכשל שווה
        # יותר מכלום, וניסיון פענוח על חצי דף עדיף על ויתור.
        pass
    text = "\n".join(parser.chunks)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text[: (limit or MAX_CONDENSED)]


# מי מביא את הדף. auto בוחר את הטוב ביותר שזמין, לפי הסדר:
# ScraperAPI (עוקף חסימת IP ומריץ JS אצלם) -> דפדפן מקומי -> urllib.
FETCHER = os.environ.get("CATALOG_FETCHER", "auto").strip().lower()


def fetcher_kind():
    """איזה מסלול הבאה יפעל בפועל. מוצג במסכים ובכלי הבדיקה."""
    from . import scraperapi

    if FETCHER in {"scraperapi", "browser", "direct"}:
        return FETCHER
    if scraperapi.configured():
        return "scraperapi"
    from .browser import browser_available

    return "browser" if browser_available() else "direct"


def fetcher_available(needs_js=False):
    """האם יש במה להביא דפים - ובאיכות שהמקור צריך.

    ``needs_js`` הוא ההבדל בין "אפשר להביא" לבין "אפשר להביא את מה
    שצריך": מול אתר שבונה את התוצאה ב-JavaScript, ``urllib`` יחזיר שלד
    ריק, המודל יאמר בצדק "לא נמצא", ואיש לא יידע שהתשובה שגויה. עדיף
    שהמסך יאמר שהשליפה כבויה.
    """
    kind = fetcher_kind()
    if kind == "scraperapi":
        from . import scraperapi

        return scraperapi.configured()
    if kind == "browser":
        from .browser import browser_available

        return browser_available()
    return not needs_js


def default_fetcher(wait_selector=None, fill_selector=None, fill_value=None,
                    submit_selector=None):
    """פונקציית ההבאה למקור שצריך דף.

    ``fill_selector`` הוא חיפוש דרך טופס, ורק דפדפן יודע לעשות אותו -
    ScraperAPI מביא כתובת ומחזיר HTML. לכן בקשה שדורשת אינטראקציה
    מקבלת דפדפן גם כשההגדרה היא ScraperAPI, ואם אין דפדפן היא נכשלת
    בהודעה שאומרת את זה במקום להביא בשקט את הדף הלא נכון.
    """
    kind = fetcher_kind()
    needs_interaction = bool(fill_selector and fill_value)

    if kind == "scraperapi" and not needs_interaction:
        from .scraperapi import ScraperApiFetcher

        return ScraperApiFetcher()
    if kind == "direct" and not needs_interaction:
        return fetch

    from .browser import BrowserFetcher, browser_available

    if not browser_available():
        if needs_interaction:
            raise FetchError(
                "החיפוש הזה דורש מילוי טופס, וזה אפשרי רק בדפדפן. "
                "התקן אותו (playwright install chromium) או הגדר כתובת "
                "חיפוש ישירה במקום סלקטור."
            )
        return fetch
    return BrowserFetcher(
        wait_selector=wait_selector,
        fill_selector=fill_selector,
        fill_value=fill_value,
        submit_selector=submit_selector,
    )


def flatten_xml(text, limit=None):
    """XML של API -> שורות קריאות, לשליחה למודל.

    מדוע לא לפרסר את הסכימה ישירות: לכל חבילת רישיון אצל ספק הקטלוג יש
    שדות אחרים, והם משתנים. השטחה לשורות "תג: מפתח=ערך" שומרת את כל
    המידע ומשאירה למודל להחליט מה בו הוא המק"ט - אותה חלוקת עבודה
    שנבחרה עבור HTML, ומאותה סיבה.
    """
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring((text or "").strip())
    except ET.ParseError:
        return (text or "")[: (limit or MAX_CONDENSED)]

    lines = []

    def walk(node, depth=0):
        pad = "  " * depth
        attrs = " ".join(f"{k}={v}" for k, v in node.attrib.items() if v)
        body = (node.text or "").strip()
        tag = node.tag.split("}")[-1]
        if attrs or body:
            lines.append(f"{pad}{tag}: {attrs} {body}".rstrip())
        elif len(node):
            lines.append(f"{pad}{tag}:")
        for child in node:
            walk(child, depth + 1)

    walk(root)
    return "\n".join(lines)[: (limit or MAX_CONDENSED)]


# --------------------------------------------------------------------------
# פענוח במודל
# --------------------------------------------------------------------------

def parser_available():
    """האם יש SDK ומפתח לפענוח הדף."""
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return bool(
        os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    )


def ask_model(prompt, client=None, max_tokens=3000):
    """שולח הנחיה ומחזיר את ה-JSON שבתשובה. מרים חריגה בכשל.

    אין כאן כלי חיפוש רשת: את הדף כבר הבאנו, והמודל רק קורא אותו.
    זה מה שהופך את הצעד לזול, מהיר וממוקד ברכב שביקשנו.
    """
    from ..parts_discovery import _json_from

    if client is None:
        import anthropic

        client = anthropic.Anthropic()
    response = client.messages.create(
        model=PARSE_MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(
        block.text for block in response.content if getattr(block, "type", "") == "text"
    )
    payload = _json_from(text)
    if payload is None:
        raise ValueError("התשובה מהמודל אינה JSON תקין")
    return payload
