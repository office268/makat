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

from . import trace
from ..taxonomy import type_name
from .base import (Candidate, CatalogSource, FetchError, ask_model,
                   bounced_to_ancestor, condense, fetch, landed_at,
                   parser_available)

# תבנית אחת, או כמה מופרדות ב-"|". כמה, כי כתובת חיפוש שלדה של אתר
# שאין לו תיעוד היא ניחוש, וניחוש אחד לכל פריסה הופך כיוון לשעה. עם
# רשימה, ריצה אחת בודקת את כולן והיומן אומר איזו ענתה.
#
# ברירת המחדל נבדקה מול האתר החי: שלדה של טויוטה מחזירה עמוד קטלוג
# מזוהה ("Toyota Parts Catalogs RAV4 2014 ZSA44R-ANXMPW") עם קבוצות
# ותרשימים - בדיוק המבנה שהצעד השני הולך אליו. שלדה שאינה מוכרת
# מחזירה "Nothing found" ב-200 ובלי הפניה, כלומר הענף הנכון של
# "האתר ענה, אין כאן כזה חלק".
#
# הגבול שלה, וכדאי לדעת אותו: הקטלוגים הם טויוטה, לקסוס, ניסאן,
# אינפיניטי, מיצובישי, סובארו, יונדאי, קיה, סוזוקי, מאזדה, הונדה,
# איסוזו, רנו, וולוו, קרייזלר, ג'יפ, דודג' ורם. פיג'ו, סיטרואן,
# סקודה ופולקסווגן אינם שם, והאתר עצמו מודיע שפענוח שלדה נתמך כרגע
# לטויוטה בלבד. ‏7zap, שנוסה קודם, גובה מנוי על החלק הזה בדיוק.
URL_TEMPLATE = os.environ.get(
    "EPC_VIN_URL", "https://partsouq.com/en/search/all?q={vin}"
)
SOURCE_NAME = os.environ.get("EPC_SOURCE_NAME", "קטלוג יצרן לפי שלדה")
MAX_HOPS = int(os.environ.get("EPC_MAX_HOPS", 2))


def templates():
    """התבניות המוגדרות, לפי הסדר."""
    return [part.strip() for part in URL_TEMPLATE.split("|") if part.strip()]


def build_url(vin):
    """הכתובת מהתבנית הראשונה - זו שתנוסה קודם."""
    urls = build_urls(vin)
    return urls[0] if urls else ""


def build_urls(vin):
    return [template.format(vin=vin, VIN=vin) for template in templates()]


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
    # הצעד הנוסף עולה בקשת רשת וקריאת מודל. דף מותג כללי אינו מקרב
    # לחלק של *הרכב הזה*, ולכן הוא בזבוז של שניהם - וכך זה נראה בשטח:
    # חיפוש שלדה שנדחף לדף הבית קיבל הצעה ללכת לדף המותג, ומשם לכלום.
    narrow = (
        """
כללי הכתובת להמשך:
- רק כתובת שמצמצמת אל *הרכב הזה*: כזו שנושאת את מספר השלדה, או את
  הדגם והדור המדויקים שלו, או את קבוצת הקטלוג של החלק המבוקש.
- אל תחזיר את דף הבית, דף מותג כללי, רשימת קטלוגים, החלפת שפה או
  אזור, או דף שיווקי. אם אין בדף כתובת שמצמצמת - החזר ריק.
- בלי סימן # ומה שאחריו. הוא אינו נשלח לשרת ולא ישנה את הדף שנקבל.
"""
        if hops_left > 0
        else ""
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
{narrow}"""


class EpcVinSource(CatalogSource):
    key = "epc"
    name = SOURCE_NAME
    tier = "oem"
    needs_vin = True

    def available(self):
        return bool(URL_TEMPLATE) and parser_available()

    def _follow(self, start_url, vehicle, part_type, get_page, client):
        """תבנית אחת: הבאה, פענוח, ואם צריך צעד נוסף. מחזיר (מה שנמצא, כתובת)."""
        url = start_url
        found = []
        for hop in range(MAX_HOPS):
            trace.note(f"  — צעד {hop + 1}/{MAX_HOPS} —")
            try:
                html = get_page(url)
            except FetchError as exc:
                if hop == 0:
                    raise
                # הצעד הנוסף הוא בונוס, לא סיבה להפיל את השליפה
                trace.note(f"  הצעד הנוסף נכשל, ממשיכים עם מה שיש: {exc}")
                break
            # ‏200 ודף תקין אינם "הגענו": אתר שאינו מכיר את כתובת החיפוש
            # מחזיר את דף הבית שלו, והתשובה שהמשתמש רואה היא "החלק לא
            # קיים לרכב הזה" - שקר, ובכיוון שאי אפשר לפעול לפיו.
            if bounced_to_ancestor(url):
                raise FetchError(
                    f"האתר לא הכיר את הכתובת ודחף אותנו ל-{landed_at()} . "
                    "התבנית שהוגדרה ב-EPC_VIN_URL אינה כתובת חיפוש שלדה "
                    "תקפה באתר הזה."
                )
            payload = ask_model(
                build_prompt(vehicle, part_type, condense(html, url), url,
                             MAX_HOPS - hop - 1),
                client=client,
            )
            found = payload.get("parts") or []
            next_url = str(payload.get("next_url") or "").strip()
            # שלוש התשובות האלה הן ההבדל בין "האתר לא מכיר את הרכב",
            # "הגענו לדף הנכון והחלק לא שם" ו"זה דף ביניים": בלעדיהן
            # כל שלושתן נראות על המסך כ'לא החזיר מק"ט'.
            trace.note(
                f'    תוצאת הפענוח: {len(found)} מק"טים · '
                f"הרכב אושר בדף: {'כן' if payload.get('vehicle_confirmed') else 'לא'} · "
                f"המשך מוצע: {next_url or '—'}"
            )
            for raw in found:
                trace.note(
                    f"      · {raw.get('oe_number') or '?'} "
                    f"[{raw.get('confidence') or 'low'}] {raw.get('name') or ''}"
                )
            if found:
                break
            if not next_url or next_url == url:
                trace.note("    אין המשך לעקוב אחריו - עוצרים כאן.")
                break
            url = next_url
        return found, url

    def lookup(self, vehicle, part_type, oem_numbers=(), fetcher=None, client=None):
        vin = (vehicle.get("vin") or "").strip()
        if not vin:
            return []
        get_page = fetcher or fetch
        urls = build_urls(vin)
        trace.note(
            f'{self.name}: שלדה {vin} · חלק "{type_name(part_type)}" · '
            f"{len(urls)} תבניות · עד {MAX_HOPS} צעדים לכל אחת"
        )
        if not urls:
            raise FetchError("לא הוגדרה כתובת קטלוג (EPC_VIN_URL).")
        # תבנית שאינה נושאת את השלדה אינה יכולה להחזיר תשובה *לרכב הזה*,
        # וזו שגיאת הגדרה שכדאי לראות לפני שמאשימים את האתר.
        for template in templates():
            if "{vin}" not in template and "{VIN}" not in template:
                trace.note(f"⚠ תבנית בלי {{vin}} - לא תוכל לזהות רכב: {template}")

        found, source_url, failure, answered = [], urls[0], None, False
        for index, start in enumerate(urls, 1):
            trace.note(f"— תבנית {index}/{len(urls)}: {start} —")
            try:
                found, source_url = self._follow(
                    start, vehicle, part_type, get_page, client
                )
            except FetchError as exc:
                failure = failure or exc
                trace.note(f"  התבנית הזו לא עבדה: {exc}")
                continue
            answered = True
            if found:
                break
        # *כל* התבניות נפלו: זו תקלה, לא "לא נמצא". ההבחנה הזו היא מה
        # שמונע מ"האתר לא ענה" להישמר במטמון כתשובה שלילית לחודשיים.
        # תבנית אחת שענתה "אין כאן כזה חלק" היא תשובה, וגוברת על אחות
        # שנפלה - אחרת תבנית שבורה ברשימה הייתה מסתירה אותה.
        if not answered and failure is not None:
            raise failure
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
