"""Laximo: מספר שלדה -> רכב מדויק -> מק"ט מקורי מקטלוג היצרן.

Laximo הוא ספק קטלוגי היצרן (OEM) - אותם קטלוגים שהמוסך המורשה עובד
מולם, עם תרשימי פיצוץ וחיפוש לפי מספר שלדה. זה בדיוק השלב שחסר לנו:
המרשם הממשלתי נותן ``misgeret``, ו-Laximo הופך אותו לרכב מדויק ולמק"ט
המקורי של החלק המבוקש.

**מסלול אחד: הקטלוג בדפדפן.** היה כאן גם מסלול API רשמי (בקשה חתומה
ב-``md5(command + password)``), והוא הוסר. הסיבה אינה טכנית אלא שהוא
דרש חשבון ``LAXIMO_LOGIN``/``LAXIMO_PASSWORD`` שאין, וקוד שאי אפשר
להריץ אינו מסלול גיבוי - הוא ענף מת שכל קורא צריך לפסוח עליו ושכל
בדיקה מזייפת. TecDoc עדיין מחזיק את שני המסלולים שלו; ההסרה כאן היא
על Laximo בלבד.

מה שנשאר הוא ``web``: הקטלוג נפתח ב-Chromium דרך Playwright, כי Laximo
בונה את התוצאה ב-JavaScript ו-``urllib`` מחזיר ממנו שלד ריק. מכאן ש-
``needs_js = True`` אינו גחמה אלא תנאי זמינות - בלי דפדפן או ScraperAPI
המקור מכבה את עצמו, במקום להביא בשקט דף ריק ולהחזיר "לא נמצא" שגוי.

ה-HTML שחוזר נקרא על ידי המודל ולא על ידי סלקטורים: מבנה הדף משתנה בין
גרסאות, סלקטור נשבר בשקט, והמק"ט הנכון הוא מה שצריך לצאת בכל מקרה.
"""
import os

from ..taxonomy import type_name
from .base import (
    Candidate,
    CatalogSource,
    FetchError,
    ask_model,
    condense,
    default_fetcher,
    fetcher_available,
    parser_available,
)

WEB_URL = os.environ.get("LAXIMO_WEB_URL", "https://laximo.ru/search?type=vin&q={vin}")
WEB_WAIT_SELECTOR = os.environ.get("LAXIMO_WEB_WAIT", "").strip() or None
# כשהחיפוש אינו כתובת אלא טופס: השדה שממלאים והכפתור שלוחצים.
# בלי SUBMIT נשלח Enter, וזה מספיק ברוב שדות החיפוש.
WEB_INPUT = os.environ.get("LAXIMO_WEB_INPUT", "").strip() or None
WEB_SUBMIT = os.environ.get("LAXIMO_WEB_SUBMIT", "").strip() or None

TIMEOUT = float(os.environ.get("LAXIMO_TIMEOUT", 20))


def build_web_url(vin):
    return WEB_URL.format(vin=vin, VIN=vin)


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
    needs_js = True   # התוצאה נבנית ב-JavaScript אחרי טעינת הדף

    def available(self):
        return (
            parser_available()
            and bool(WEB_URL)
            and fetcher_available(needs_js=self.needs_js)
        )

    def _payload(self, vin, fetcher=None):
        """מביא את התשובה הגולמית, ומחזיר (טקסט לשליחה למודל, מקור)."""
        url = build_web_url(vin)
        if fetcher is not None:
            return condense(fetcher(url), url), url
        from .browser import BrowserError

        try:
            html = default_fetcher(
                wait_selector=WEB_WAIT_SELECTOR,
                fill_selector=WEB_INPUT,
                fill_value=vin if WEB_INPUT else None,
                submit_selector=WEB_SUBMIT,
            )(url, timeout=TIMEOUT)
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
