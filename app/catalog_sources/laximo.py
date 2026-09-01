"""Laximo: מספר שלדה -> רכב מדויק -> מק"ט מקורי מקטלוג היצרן.

Laximo הוא ספק קטלוגי היצרן (OEM) - אותם קטלוגים שהמוסך המורשה עובד
מולם, עם תרשימי פיצוץ וחיפוש לפי מספר שלדה. זה בדיוק השלב שחסר לנו:
המרשם הממשלתי נותן ``misgeret``, ו-Laximo הופך אותו לרכב מדויק ולמק"ט
המקורי של החלק המבוקש.

**שני מסלולים, אותו פלט.**

``api`` - השירות הרשמי (Laximo Cat). דורש ``LAXIMO_LOGIN`` ו-
``LAXIMO_PASSWORD``. הבקשה חתומה: ``md5(command + password)`` משמש
כסיסמת ה-Basic Auth, וה-login נשאר שם המשתמש. זה המסלול המועדף - הוא
מהיר, יציב, ומורשה. מחרוזת הפקודה עצמה שונה בין חבילות רישיון, ולכן
היא ``LAXIMO_VIN_COMMAND`` ולא קבוע בקוד.

``web`` - הקטלוג בדפדפן, דרך Playwright. משמש כשאין עדיין חשבון API,
או כשלחשבון יש גישת web בלבד. Laximo בונה את התוצאה ב-JavaScript,
ולכן ``urllib`` מחזיר ממנו שלד ריק ו-Chromium הוא לא גחמה.

בשני המסלולים הטקסט שחוזר - XML או HTML - נקרא על ידי המודל ולא על
ידי סלקטורים. סכימת ה-API משתנה בין חבילות, מבנה הדף משתנה בין גרסאות,
והמק"ט הנכון הוא מה שצריך לצאת בשני המקרים.
"""
import hashlib
import os
import urllib.parse
import urllib.request

from ..taxonomy import type_name
from .base import (
    Candidate,
    CatalogSource,
    FetchError,
    USER_AGENT,
    ask_model,
    condense,
    flatten_xml,
    parser_available,
)

MODE = os.environ.get("LAXIMO_MODE", "auto").strip().lower()  # auto | api | web

LOGIN = os.environ.get("LAXIMO_LOGIN", "").strip()
PASSWORD = os.environ.get("LAXIMO_PASSWORD", "").strip()
API_URL = os.environ.get("LAXIMO_API_URL", "https://ws.laximo.ru/ec.api.php")
# הפקודה מקבלת את השלדה. הפורמט לפי תיעוד Laximo לחבילה שברשותך -
# לכן משתנה סביבה, ולא קבוע: חבילה אחרת = פקודה אחרת, בלי פריסה.
VIN_COMMAND = os.environ.get(
    "LAXIMO_VIN_COMMAND", "FindVehicleByVIN:Locale=en_US|vin={vin}|localized=true"
)
LOCALE = os.environ.get("LAXIMO_LOCALE", "en_US")

WEB_URL = os.environ.get("LAXIMO_WEB_URL", "https://laximo.ru/search?type=vin&q={vin}")
WEB_WAIT_SELECTOR = os.environ.get("LAXIMO_WEB_WAIT", "").strip() or None

TIMEOUT = float(os.environ.get("LAXIMO_TIMEOUT", 20))


def api_configured():
    return bool(LOGIN and PASSWORD)


def sign(command, password=None):
    """סיסמת ה-Basic Auth של Laximo: md5 של הפקודה ואחריה הסיסמה."""
    secret = password if password is not None else PASSWORD
    return hashlib.md5(f"{command}{secret}".encode("utf-8")).hexdigest()


def build_command(vin):
    return VIN_COMMAND.format(vin=vin, VIN=vin, locale=LOCALE)


def build_web_url(vin):
    return WEB_URL.format(vin=vin, VIN=vin)


def call_api(command, url=None, login=None, password=None, timeout=None):
    """קריאה חתומה אחת ל-Laximo. מחזיר את גוף התשובה כטקסט."""
    endpoint = url or API_URL
    user = login if login is not None else LOGIN
    token = sign(command, password)
    request = urllib.request.Request(
        endpoint,
        data=urllib.parse.urlencode({"request": command}).encode("utf-8"),
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": "Basic " + _basic(user, token),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout or TIMEOUT) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")
    except Exception as exc:  # רשת, הרשאה, שירות
        raise FetchError(f"Laximo API: {exc}") from exc


def _basic(user, token):
    import base64

    return base64.b64encode(f"{user}:{token}".encode("utf-8")).decode("ascii")


def build_prompt(vehicle, part_type, payload, origin):
    return f"""לפניך תשובה מקטלוג חלפים מקורי (Laximo) על חיפוש לפי מספר שלדה.

הרכב מהמרשם:
  יצרן: {vehicle.get('make') or '—'}
  דגם: {vehicle.get('model') or '—'}
  שנה: {vehicle.get('year') or '—'}
  קוד דגם: {vehicle.get('model_code') or '—'}
  קוד מנוע: {vehicle.get('engine_code') or '—'}
  מספר שלדה: {vehicle.get('vin') or '—'}

החלק המבוקש: {type_name(part_type)}

מקור התשובה: {origin}

התשובה:
---
{payload}
---

החזר JSON בלבד, בלי טקסט נוסף:
{{"parts": [
  {{"oe_number": "המק\\"ט המקורי בדיוק כפי שמופיע",
    "name": "שם החלק כפי שמופיע",
    "image_url": "כתובת תמונה או תרשים פיצוץ מהתשובה, או ריק",
    "variant": "מזהה הרכב/הווריאנט אצל הקטלוג (vehicleid, ssd, קבוצה), או ריק",
    "confidence": "high" או "low",
    "note": "משפט קצר בעברית - על מה התבססת"}}
],
"vehicle_confirmed": true אם התשובה מזהה את הרכב שלמעלה, אחרת false,
"next_url": "כתובת מהתשובה שתוביל לחלק המבוקש, אם החלק עצמו לא מופיע כאן"}}

כללים מחייבים:
- רק מק"טים שהתשובה קושרת לרכב הזה. אל תיקח מק"ט מרשימת "רכבים דומים".
- "high" רק אם התשובה קושרת את המק"ט לשלדה או לווריאנט של הרכב הזה.
- אל תמציא מק"ט, אל תשלים ספרות ואל תמציא כתובת תמונה.
- אם התשובה היא שגיאה או "לא נמצא", החזר רשימה ריקה ואת השגיאה ב-note.
- עד 5 מק"טים.
"""


class LaximoSource(CatalogSource):
    key = "laximo"
    name = "Laximo · קטלוג יצרן לפי שלדה"
    tier = "oem"
    needs_vin = True

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
        from .browser import browser_available

        return bool(WEB_URL) and browser_available()

    def _payload(self, vin, fetcher=None):
        """מביא את התשובה הגולמית, ומחזיר (טקסט לשליחה למודל, מקור)."""
        if fetcher is not None:
            raw = fetcher(build_web_url(vin))
            return condense(raw, build_web_url(vin)), build_web_url(vin)
        if self.use_api():
            command = build_command(vin)
            return flatten_xml(call_api(command)), f"{API_URL} · {command}"
        from .browser import BrowserError, BrowserFetcher

        url = build_web_url(vin)
        try:
            html = BrowserFetcher(wait_selector=WEB_WAIT_SELECTOR)(url, timeout=TIMEOUT)
        except BrowserError as exc:
            raise FetchError(str(exc)) from exc
        return condense(html, url), url

    def lookup(self, vehicle, part_type, oem_numbers=(), fetcher=None, client=None):
        vin = (vehicle.get("vin") or "").strip()
        if not vin:
            return []
        payload, origin = self._payload(vin, fetcher=fetcher)
        answer = ask_model(
            build_prompt(vehicle, part_type, payload, origin), client=client
        )
        brand = (vehicle.get("make") or "").strip().split()
        brand = brand[0] if brand else ""

        candidates = []
        for raw in answer.get("parts") or []:
            number = str(raw.get("oe_number") or "").strip()
            if not number:
                continue
            candidates.append(
                Candidate(
                    part_number=number,
                    manufacturer=brand,
                    tier="oem",
                    oe_number=number,
                    oe_brand=brand,
                    image_url=str(raw.get("image_url") or "").strip()[:500],
                    source_url=origin[:500],
                    source_key=self.key,
                    variant_key=str(raw.get("variant") or "").strip()[:80],
                    confidence=str(raw.get("confidence") or "low").lower(),
                    note=str(raw.get("note") or "").strip()[:300],
                    extra={"name": str(raw.get("name") or "").strip()[:200]},
                )
            )
        return candidates
