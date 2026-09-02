"""הבאה דרך ScraperAPI, כשהאתר לא מוכן לדבר איתנו ישירות.

שלוש בעיות שהשירות הזה פותר, וכולן שלנו:

1. **חסימת IP של ענן.** אתרי קטלוג מסחריים חוסמים טווחי כתובות של
   ספקי אירוח, ו-Railway הוא בדיוק כזה. ScraperAPI מסובב כתובות
   ביתיות, כך שהבקשה נראית כמו גלישה ולא כמו שרת.

2. **JavaScript בלי דפדפן אצלנו.** ``render=true`` מריץ את הדף אצלם.
   זה מייתר את Chromium בתמונה - 400MB, וגם את כל הסיפור של מספרי
   build ושל thread ייעודי.

3. **מדינה.** קטלוג מציג מחירים וזמינות לפי מיקום, ו-``country_code``
   קובע מאיפה נראה שהגענו.

**מה הוא לא פותר.** אין כאן מילוי טופס ולחיצה - השירות מביא כתובת
ומחזיר HTML. איפה שצריך אינטראקציה, מסלול הדפדפן נשאר הדרך.

**זמן.** עיבוד עם ``render=true`` לוקח עשרות שניות. בקשת שלב אחת
במסך היא הבאה אחת ועוד קריאה למודל, ו-gunicorn הורג בקשה אחרי 60
שניות - ולכן עם המסלול הזה צריך להעלות את ``WEB_TIMEOUT``. ראה README.
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

API_URL = os.environ.get("SCRAPERAPI_URL", "https://api.scraperapi.com/")
API_KEY = os.environ.get("SCRAPERAPI_KEY", "").strip()
# הרצת JavaScript בצד שלהם. זה מה שמייתר את Chromium אצלנו.
RENDER = os.environ.get("SCRAPERAPI_RENDER", "1").strip() != "0"
COUNTRY = os.environ.get("SCRAPERAPI_COUNTRY", "il").strip()
# פרוקסי מגורים / פרימיום. עולה יותר קרדיטים, ועובר איפה שהרגיל נחסם.
PREMIUM = os.environ.get("SCRAPERAPI_PREMIUM", "0").strip() == "1"
# חייב להישאר מתחת ל-WEB_TIMEOUT פחות הזמן של קריאת המודל.
TIMEOUT = float(os.environ.get("SCRAPERAPI_TIMEOUT", 40))


def configured():
    return bool(API_KEY)


def build_url(url, api_key=None, render=None, country=None, premium=None):
    """כתובת הבקשה לשירות, עם הכתובת האמיתית כפרמטר."""
    params = {
        "api_key": api_key if api_key is not None else API_KEY,
        "url": url,
    }
    if RENDER if render is None else render:
        params["render"] = "true"
    country_code = COUNTRY if country is None else country
    if country_code:
        params["country_code"] = country_code
    if PREMIUM if premium is None else premium:
        params["premium"] = "true"
    return f"{API_URL}?{urllib.parse.urlencode(params)}"


def _body_hint(body):
    """שארית התשובה - רק כשהיא באמת אומרת משהו.

    גוף של תשובת שגיאה הוא לרוב דף HTML, ומאתיים התווים הראשונים שלו
    הם ``<!DOCTYPE>`` ורשימת מחלקות CSS. הדבקתם בהודעה שמגיעה למכונאי
    אינה מוסיפה מידע - היא מסתירה את המשפט שכן אומר מה קרה.
    """
    text = " ".join((body or "").split())
    if not text or text.lstrip().startswith("<") or "<html" in text[:400].lower():
        return ""
    return text[:200]


def _explain(code, body, url=None):
    """שגיאות השירות בשפה שאפשר לפעול לפיה, לא מספר סטטוס."""
    known = {
        401: "מפתח ScraperAPI שגוי או חסר (SCRAPERAPI_KEY).",
        403: "אין הרשאה לבקשה הזו - ייתכן שהיא דורשת premium.",
        # 404 כאן אינו של ScraperAPI אלא של האתר שאליו הוא פנה: הוא
        # מעביר את הסטטוס כמות שהוא. כלומר הכתובת עצמה אינה קיימת שם,
        # וזו הגדרה שגויה ולא תקלה חולפת.
        404: "כתובת הקטלוג שהוגדרה אינה קיימת באתר (404).",
        429: "נגמרו הקרדיטים או חריגה מקצב הבקשות ב-ScraperAPI.",
        500: "ScraperAPI לא הצליח להביא את הדף (האתר חסם או נפל).",
    }
    detail = known.get(code, f"ScraperAPI החזיר {code}")
    # הכתובת שנוסתה היא הפרט השימושי ביותר בשגיאת 404, והיא נעדרה
    parts = [detail, url or "", _body_hint(body)]
    return " ".join(part for part in parts if part).strip()


class ScraperApiFetcher:
    """מתאם לחתימת ה-fetcher של המקורות: ``fetcher(url, timeout=None)``."""

    def __init__(self, api_key=None, render=None, country=None, premium=None):
        self.api_key = api_key
        self.render = render
        self.country = country
        self.premium = premium

    def __call__(self, url, timeout=None):
        from . import trace
        from .base import (FetchError, USER_AGENT, allowed_by_robots, content_type,
                           describe_page)

        render = RENDER if self.render is None else self.render
        trace.note(f"→ ScraperAPI: {url} (render={'כן' if render else 'לא'})")
        if not (self.api_key or API_KEY):
            trace.note("  ← אין מפתח")
            raise FetchError("אין SCRAPERAPI_KEY.")
        # השירות מביא בשמנו, ולכן ה-robots של האתר עדיין מחייב אותנו.
        if not allowed_by_robots(url):
            trace.note("  ← נחסם ב-robots.txt")
            raise FetchError(f"robots.txt של האתר אוסר את הכתובת: {url}")

        request = urllib.request.Request(
            build_url(url, self.api_key, self.render, self.country, self.premium),
            headers={"User-Agent": USER_AGENT},
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=timeout or TIMEOUT) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                body = response.read().decode(charset, errors="replace")
                describe_page(
                    body, url,
                    status=getattr(response, "status", None),
                    elapsed=time.monotonic() - started,
                    content_type=content_type(response),
                )
                return body
        except urllib.error.HTTPError as exc:
            trace.note(f"  ← HTTP {exc.code} {exc.reason}")
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            raise FetchError(_explain(exc.code, body, url)) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            trace.note(f"  ← לא נענה: {type(exc).__name__}: {exc}")
            raise FetchError(f"ScraperAPI לא נגיש: {exc}") from exc


def account():
    """מצב החשבון: כמה קרדיטים נותרו. לבדיקה מהירה מהשורה."""
    from .base import FetchError, USER_AGENT

    url = f"https://api.scraperapi.com/account?api_key={API_KEY}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise FetchError(_explain(exc.code, "")) from exc
    except Exception as exc:
        raise FetchError(f"ScraperAPI לא נגיש: {exc}") from exc
