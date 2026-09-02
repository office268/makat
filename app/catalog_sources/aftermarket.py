"""שלב שני: מק"ט מקורי -> מק"טים חלופיים, עם תמונת מוצר.

המכונאי לא מזמין מק"ט מקורי. הוא מזמין את החלופי שמתאים לו במחיר
ובזמן אספקה, ומספר ה-OE הוא מה שמאשר לו שזה אותו חלק. לכן השלב הזה
לא מחפש לפי דגם - הוא מחפש לפי המספר שהשלב הקודם הוציא מהשלדה, וכל
מה שהוא מחזיר קשור למספר הזה.

זו גם הסיבה שהוא רץ גם כשאין שלדה: מק"ט מקורי שכבר יושב בקטלוג
(``CrossReference`` מסוג OEM) הוא מפתח חיפוש טוב בדיוק באותה מידה.
"""
import os

from ..taxonomy import type_name
from . import trace
from .base import (Candidate, CatalogSource, FetchError, ask_model, condense,
                   default_fetcher, fetch, fetcher_name, parser_available)

URL_TEMPLATE = os.environ.get(
    "AFTERMARKET_URL", "https://www.autodoc.co.il/spare-parts/search?keyword={oem}"
)
SOURCE_NAME = os.environ.get("AFTERMARKET_SOURCE_NAME", "קטלוג חלופים")
# כמה מספרי OE נבדקים בשליפה אחת. כל אחד הוא בקשה לאתר וקריאה למודל.
MAX_NUMBERS = int(os.environ.get("AFTERMARKET_MAX_NUMBERS", 2))


def build_url(oem):
    return URL_TEMPLATE.format(oem=oem, OEM=oem)


def build_prompt(vehicle, part_type, oem, page, url):
    return f"""לפניך תוכן של עמוד מקטלוג חלפים, שהתקבל מחיפוש לפי מספר מקורי.

המספר המקורי שחיפשנו: {oem}
החלק המבוקש: {type_name(part_type)}
הרכב: {vehicle.get('make') or '—'} {vehicle.get('model') or ''} {vehicle.get('year') or ''}

כתובת העמוד: {url}

תוכן העמוד (טקסט, קישורים כ-[LINK ...] ותמונות כ-[IMG כתובת | תיאור]):
---
{page}
---

החזר JSON בלבד, בלי טקסט נוסף:
{{"parts": [
  {{"part_number": "מק\\"ט היצרן של החלף",
    "manufacturer": "שם יצרן החלף",
    "image_url": "כתובת תמונת המוצר מהעמוד, או ריק",
    "price_eur": מספר או null,
    "confidence": "high" או "low",
    "note": "משפט קצר בעברית"}}
]}}

כללים מחייבים:
- רק חלפים שהעמוד מציג כתחליף למספר {oem}. עמוד חיפוש מציג גם "מוצרים
  דומים" ופרסומות - אל תכלול אותם.
- אם החלף שייך ליצרן רכב אחר, אל תכלול אותו.
- "high" רק אם העמוד קושר את החלף למספר המקורי במפורש.
- אל תמציא מק"ט ואל תמציא כתובת תמונה.
- עד 6 חלפים.
"""


class AftermarketSource(CatalogSource):
    key = "aftermarket"
    name = SOURCE_NAME
    tier = "aftermarket"
    needs_vin = False

    def available(self):
        return bool(URL_TEMPLATE) and parser_available()

    def lookup(self, vehicle, part_type, oem_numbers=(), fetcher=None, client=None):
        numbers = [str(n).strip() for n in oem_numbers if str(n or "").strip()]
        # "אין מספרים" הוא הכישלון השקט של השלב הזה: הוא מחזיר רשימה
        # ריקה בלי שהתרחשה שום בקשה, ועל המסך זה נראה כמו "האתר לא
        # מצא". השורה הזו מבדילה בין השניים.
        trace.note(
            f"{self.name}: הבאה: {fetcher_name(fetcher or default_fetcher())}"
        )
        trace.note(
            f"{self.name}: {len(numbers)} מספרים מקוריים לחיפוש"
            + (f" ({', '.join(numbers[:MAX_NUMBERS])})" if numbers
               else " - אין ממה להתחיל, השלב הקודם לא החזיר מק\"ט מקורי")
        )
        if not numbers:
            return []
        get_page = fetcher or default_fetcher()
        candidates = []
        seen = set()
        first_error = None
        for oem in numbers[:MAX_NUMBERS]:
            url = build_url(oem)
            trace.note(f"— מספר מקורי {oem} —")
            try:
                html = get_page(url)
            except FetchError as exc:
                trace.note(f"  דילוג: {exc}")
                first_error = first_error or exc
                continue
            payload = ask_model(
                build_prompt(vehicle, part_type, oem, condense(html, url), url),
                client=client,
            )
            rows = payload.get("parts") or []
            trace.note(f"  תוצאת הפענוח: {len(rows)} חלופים")
            for raw in rows:
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
                        oe_brand=vehicle.get("make") or "",
                        image_url=str(raw.get("image_url") or "").strip()[:500],
                        price_eur=raw.get("price_eur"),
                        source_url=url[:500],
                        source_key=self.key,
                        confidence=str(raw.get("confidence") or "low").lower(),
                        note=str(raw.get("note") or "").strip()[:300],
                    )
                )
        # כשכל המספרים נכשלו בהבאה, זו תקלה ולא "לא נמצא"
        if not candidates and first_error is not None:
            raise first_error
        return candidates
