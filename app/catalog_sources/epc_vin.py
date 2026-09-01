"""שלב ראשון: מספר שלדה -> מק"ט מקורי, מקטלוג היצרן.

זה הצעד שמצדיק את כל התהליך. חיפוש לפי יצרן/דגם/שנה מחזיר את מה
שמתאים *לדגם*; חיפוש לפי שלדה מחזיר את מה שמתאים *לרכב הזה* - אותה
קורולה משנת 2016 עם שני מנועים שונים מקבלת שתי תשובות שונות, וזה
בדיוק ההבדל בין מק"ט שמתאים לבין מק"ט שחוזר למחסן.

הכתובת נבנית מתבנית שניתנת להחלפה בלי פריסה (``EPC_VIN_URL``), כי
אתרי קטלוג משנים מבנה כתובות והתבנית היא הדבר הכי שביר כאן. שינוי
מבנה *העמוד* לא שובר כלום - את העמוד קורא המודל.

עמוד תוצאות של חיפוש שלדה הוא לרוב תחנת ביניים: הוא מזהה את הרכב
ומוביל לקבוצות הקטלוג. לכן מותר לצעד אחד נוסף (``EPC_MAX_HOPS``) -
המודל מחזיר את הקישור שממנו יגיע החלק, ואנחנו הולכים לשם פעם אחת.
"""
import os

from ..taxonomy import type_name
from .base import Candidate, CatalogSource, FetchError, ask_model, condense, fetch, parser_available

URL_TEMPLATE = os.environ.get("EPC_VIN_URL", "https://7zap.com/en/search/?q={vin}")
SOURCE_NAME = os.environ.get("EPC_SOURCE_NAME", "קטלוג יצרן לפי שלדה")
MAX_HOPS = int(os.environ.get("EPC_MAX_HOPS", 2))


def build_url(vin):
    return URL_TEMPLATE.format(vin=vin, VIN=vin)


def _brand(make):
    """יצרן הרכב כפי שהוא נשמר בקטלוג: "טויוטה יפן" -> "טויוטה".

    במק"ט מקורי יצרן החלק *הוא* יצרן הרכב, ושם מלא מהמרשם היה פותח
    יצרן שני בקטלוג לצד זה שכבר קיים.
    """
    words = (make or "").strip().split()
    return words[0] if words else ""


def build_prompt(vehicle, part_type, page, url, hops_left):
    """ההנחיה: לקרוא דף אחד ולהחזיר ממנו את המק"ט המקורי, או לאן ללכת."""
    follow = (
        '"next_url": "כתובת אחת מתוך הדף שסביר שתוביל לחלק המבוקש, או ריק",\n'
        if hops_left > 0
        else '"next_url": "",\n'
    )
    return f"""לפניך תוכן של עמוד מקטלוג חלפים מקורי, שהתקבל מחיפוש לפי מספר שלדה.

הרכב:
  יצרן: {vehicle.get('make') or '—'}
  דגם: {vehicle.get('model') or '—'}
  שנה: {vehicle.get('year') or '—'}
  קוד דגם: {vehicle.get('model_code') or '—'}
  קוד מנוע: {vehicle.get('engine_code') or '—'}
  מספר שלדה: {vehicle.get('vin') or '—'}

החלק המבוקש: {type_name(part_type)}

כתובת העמוד: {url}

תוכן העמוד (טקסט, קישורים כ-[LINK ...] ותמונות כ-[IMG כתובת | תיאור]):
---
{page}
---

החזר JSON בלבד, בלי טקסט נוסף:
{{"parts": [
  {{"oe_number": "המק\\"ט המקורי בדיוק כפי שמופיע בעמוד",
    "name": "שם החלק כפי שמופיע",
    "image_url": "כתובת תמונה או תרשים פיצוץ מהעמוד שמתאר את החלק, או ריק",
    "variant": "הווריאנט/קבוצת הקטלוג שאליה החלק שייך, או ריק",
    "confidence": "high" או "low",
    "note": "משפט קצר בעברית - על מה התבססת"}}
],
{follow}"vehicle_confirmed": true אם העמוד מאשר שזה הרכב שלמעלה, אחרת false}}

כללים מחייבים:
- רק חלקים שהעמוד מציג כשייכים לרכב הזה. עמוד קטלוג מציג גם חלקים של
  רכבים אחרים - אל תכלול אותם.
- "high" רק אם העמוד קושר את המק"ט לשלדה או לווריאנט של הרכב הזה.
- אל תמציא מק"ט ואל תשלים ספרות. אם אין בעמוד, החזר רשימה ריקה.
- אל תחזיר תמונה שאינה מהעמוד. עדיף בלי תמונה מאשר תמונה לא נכונה.
- עד 5 חלקים.
"""


class EpcVinSource(CatalogSource):
    key = "epc"
    name = SOURCE_NAME
    tier = "oem"
    needs_vin = True

    def available(self):
        return bool(URL_TEMPLATE) and parser_available()

    def lookup(self, vehicle, part_type, oem_numbers=(), fetcher=None, client=None):
        vin = (vehicle.get("vin") or "").strip()
        if not vin:
            return []
        get_page = fetcher or fetch
        url = build_url(vin)
        found = []
        for hop in range(MAX_HOPS):
            try:
                html = get_page(url)
            except FetchError:
                if hop == 0:
                    raise
                break  # הצעד הנוסף הוא בונוס, לא סיבה להפיל את השליפה
            payload = ask_model(
                build_prompt(vehicle, part_type, condense(html, url), url,
                             MAX_HOPS - hop - 1),
                client=client,
            )
            found = payload.get("parts") or []
            if found:
                break
            next_url = str(payload.get("next_url") or "").strip()
            if not next_url or next_url == url:
                break
            url = next_url

        source_url = url
        candidates = []
        for raw in found:
            number = str(raw.get("oe_number") or "").strip()
            if not number:
                continue
            candidates.append(
                Candidate(
                    part_number=number,
                    manufacturer=_brand(vehicle.get("make")),
                    tier="oem",
                    oe_number=number,
                    oe_brand=_brand(vehicle.get("make")),
                    image_url=str(raw.get("image_url") or "").strip()[:500],
                    source_url=source_url[:500],
                    source_key=self.key,
                    variant_key=str(raw.get("variant") or "").strip()[:80],
                    confidence=str(raw.get("confidence") or "low").lower(),
                    note=str(raw.get("note") or "").strip()[:300],
                    extra={"name": str(raw.get("name") or "").strip()[:200]},
                )
            )
        return candidates
