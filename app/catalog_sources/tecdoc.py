"""TecDoc: מק"ט מקורי -> חלפים חלופיים, עם תמונת מוצר.

TecDoc (TecAlliance) הוא הסטנדרט של ענף החלפים: מי מייצר תחליף לאיזה
מספר מקורי, לאיזה רכב הוא מתאים, ואיך הוא נראה. הוא נכנס בדיוק אחרי
Laximo - Laximo מוציא מהשלדה את המספר המקורי, ו-TecDoc אומר מה המוסך
באמת יכול להזמין במקומו.

**שני מסלולים, אותו פלט.**

``api`` - השירות הרשמי של TecAlliance. דורש ``TECDOC_API_KEY`` ומזהה
ספק (``TECDOC_PROVIDER``). זה המסלול המורשה, והוא היחיד שמותר לשימוש
מסחרי בתמונות ובנתונים. גוף הבקשה שונה בין חבילות רישיון ולכן הוא
תבנית (``TECDOC_QUERY``) ולא קבוע בקוד.

``web`` - הקטלוג בדפדפן דרך Playwright, למי שיש לו גישת web בלבד.

בשני המסלולים המודל קורא את התשובה. סכימת ה-JSON של TecDoc עמוקה
ומשתנה בין חבילות, ומבנה הדף משתנה בין גרסאות - ובשני המקרים מה שצריך
לצאת הוא אותו דבר: מק"ט, יצרן ותמונה.
"""
import json
import os
import urllib.error
import urllib.request

from ..taxonomy import type_name
from .base import (
    MAX_CONDENSED,
    Candidate,
    CatalogSource,
    FetchError,
    USER_AGENT,
    ask_model,
    condense,
    default_fetcher,
    fetcher_available,
    parser_available,
)

MODE = os.environ.get("TECDOC_MODE", "auto").strip().lower()  # auto | api | web

API_URL = os.environ.get(
    "TECDOC_API_URL",
    "https://webservice.tecalliance.services/pegasus-3-0/services/"
    "TecdocToCatDLB.jsonEndpoint",
)
API_KEY = os.environ.get("TECDOC_API_KEY", "").strip()
PROVIDER = os.environ.get("TECDOC_PROVIDER", "").strip()
COUNTRY = os.environ.get("TECDOC_COUNTRY", "IL")
LANG = os.environ.get("TECDOC_LANG", "en")
# תבנית גוף הבקשה. ברירת המחדל היא חיפוש לפי מספר מקורי; חבילה אחרת
# או שאילתה אחרת = משתנה סביבה אחר, בלי פריסה מחדש.
QUERY = os.environ.get("TECDOC_QUERY", "").strip()

# גם כאן ברירת המחדל היא השערה שלא אומתה. בניגוד ל-Laximo היא לא נבדקה
# מול האתר, ולכן אין לדעת אם היא עובדת - וזה בדיוק העניין: מקור שכתובתו
# משוערת ידווח על כישלון בכל שליפה. ראה README.
WEB_URL = os.environ.get(
    "TECDOC_WEB_URL", "https://webcat.tecalliance.services/search?query={oem}"
)
WEB_WAIT_SELECTOR = os.environ.get("TECDOC_WEB_WAIT", "").strip() or None
WEB_INPUT = os.environ.get("TECDOC_WEB_INPUT", "").strip() or None
WEB_SUBMIT = os.environ.get("TECDOC_WEB_SUBMIT", "").strip() or None

TIMEOUT = float(os.environ.get("TECDOC_TIMEOUT", 20))
MAX_NUMBERS = int(os.environ.get("TECDOC_MAX_NUMBERS", 2))


def api_configured():
    return bool(API_KEY and PROVIDER)


def build_query(oem):
    """גוף הבקשה ל-API. תבנית מהסביבה, או ברירת מחדל של חיפוש לפי OE."""
    if QUERY:
        return json.loads(QUERY.replace("{oem}", oem))
    return {
        "getArticles": {
            "articleCountry": COUNTRY,
            "provider": int(PROVIDER) if PROVIDER.isdigit() else PROVIDER,
            "lang": LANG,
            "searchQuery": oem,
            "searchType": 10,          # 10 = חיפוש לפי מספר מקורי (OE)
            "perPage": 20,
            "page": 1,
            "includeArticleCriteria": False,
            "includeImages": True,
            "includeGenericArticles": True,
            "includeOEMNumbers": True,
        }
    }


def build_web_url(oem):
    return WEB_URL.format(oem=oem, OEM=oem)


def call_api(oem, url=None, api_key=None, timeout=None):
    """קריאה אחת ל-TecAlliance. מחזיר את גוף התשובה כטקסט."""
    endpoint = f"{url or API_URL}?api_key={api_key or API_KEY}"
    body = json.dumps(build_query(oem)).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=body,
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout or TIMEOUT) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")
    except urllib.error.HTTPError as exc:
        raise FetchError(f"TecDoc API החזיר {exc.code}") from exc
    except Exception as exc:
        raise FetchError(f"TecDoc API: {exc}") from exc


def readable(payload, limit=None):
    """JSON עמוק -> טקסט קריא למודל, חתוך לגודל סביר."""
    try:
        return json.dumps(
            json.loads(payload), ensure_ascii=False, indent=1
        )[: (limit or MAX_CONDENSED)]
    except (ValueError, TypeError):
        return (payload or "")[: (limit or MAX_CONDENSED)]


def build_prompt(vehicle, part_type, oem, payload, origin):
    return f"""לפניך תשובה מקטלוג חלפים (TecDoc) על חיפוש לפי מספר מקורי.

המספר המקורי שחיפשנו: {oem}
החלק המבוקש: {type_name(part_type)}
הרכב: {vehicle.get('make') or '—'} {vehicle.get('model') or ''} {vehicle.get('year') or ''}
קוד מנוע: {vehicle.get('engine_code') or '—'}

מקור התשובה: {origin}

התשובה:
---
{payload}
---

החזר JSON בלבד, בלי טקסט נוסף:
{{"parts": [
  {{"part_number": "מק\\"ט היצרן של החלף",
    "manufacturer": "שם יצרן החלף",
    "image_url": "כתובת תמונת המוצר מהתשובה, או ריק",
    "price_listed": המחיר כפי שהוא בעמוד, מספר או null,
    "currency": "קוד המטבע של המחיר הזה: ILS / EUR / USD, או ריק",
    "confidence": "high" או "low",
    "note": "משפט קצר בעברית"}}
]}}

כללים מחייבים:
- רק חלפים שהתשובה מציגה כתחליף למספר {oem}. "מוצרים דומים", המלצות
  ופרסומות אינם תחליף - אל תכלול אותם.
- אם החלף שייך ליצרן רכב אחר, אל תכלול אותו.
- "high" רק אם התשובה קושרת את החלף למספר המקורי במפורש.
- אל תמציא מק"ט ואל תמציא כתובת תמונה.
- אם התשובה היא שגיאה או "לא נמצא", החזר רשימה ריקה ואת השגיאה ב-note.
- עד 6 חלפים.
"""


class TecDocSource(CatalogSource):
    key = "tecdoc"
    name = "TecDoc · קטלוג חלופים"
    tier = "aftermarket"
    needs_vin = False
    needs_js = True   # התוצאה נבנית ב-JavaScript אחרי טעינת הדף

    def use_api(self):
        if MODE == "api":
            return True
        if MODE == "web":
            return False
        return api_configured()

    def available(self):
        if not parser_available():
            return False
        if self.use_api():
            return api_configured()
        return bool(WEB_URL) and fetcher_available(needs_js=self.needs_js)

    def _payload(self, oem, fetcher=None):
        if fetcher is not None:
            url = build_web_url(oem)
            return condense(fetcher(url), url), url
        if self.use_api():
            return readable(call_api(oem)), f"{API_URL} · OE {oem}"
        from .browser import BrowserError

        url = build_web_url(oem)
        try:
            html = default_fetcher(
                wait_selector=WEB_WAIT_SELECTOR,
                fill_selector=WEB_INPUT,
                fill_value=oem if WEB_INPUT else None,
                submit_selector=WEB_SUBMIT,
            )(url, timeout=TIMEOUT)
        except BrowserError as exc:
            raise FetchError(str(exc)) from exc
        return condense(html, url), url

    def lookup(self, vehicle, part_type, oem_numbers=(), fetcher=None, client=None):
        numbers = [str(n).strip() for n in oem_numbers if str(n or "").strip()]
        if not numbers:
            return []
        candidates = []
        seen = set()
        first_error = None
        for oem in numbers[:MAX_NUMBERS]:
            try:
                payload, origin = self._payload(oem, fetcher=fetcher)
            except FetchError as exc:
                first_error = first_error or exc
                continue
            answer = ask_model(
                build_prompt(vehicle, part_type, oem, payload, origin), client=client
            )
            for raw in answer.get("parts") or []:
                number = str(raw.get("part_number") or "").strip()
                if not number or number.lower() in seen:
                    continue
                seen.add(number.lower())
                candidates.append(
                    Candidate(
                        part_number=number,
                        manufacturer=str(raw.get("manufacturer") or "").strip(),
                        tier="aftermarket",
                        oe_number=oem,
                        oe_brand=(vehicle.get("make") or "").strip().split()[0]
                        if (vehicle.get("make") or "").strip()
                        else "",
                        image_url=str(raw.get("image_url") or "").strip()[:500],
                        price_listed=raw.get("price_listed"),
                        currency=str(raw.get("currency") or "").strip()[:8].upper(),
                        source_url=origin[:500],
                        source_key=self.key,
                        confidence=str(raw.get("confidence") or "low").lower(),
                        note=str(raw.get("note") or "").strip()[:300],
                    )
                )
        if not candidates and first_error is not None:
            raise first_error
        return candidates
