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
import time

from . import toyota_groups, trace
from ..taxonomy import type_name
from .base import (Candidate, CatalogSource, Continuation, FetchError,
                   ask_model, bounced_to_ancestor, condense, default_fetcher,
                   fetch, fetcher_name, landed_at, parser_available)

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
# קטלוג יצרן הוא עץ: עמוד הרכב -> קבוצה -> תרשים -> מספרי חלקים.
# שניים לא הספיקו כדי להגיע לתרשים, וזה נמדד: הצעד הראשון זיהה את
# הרכב, השני הגיע לקבוצה, והמק"טים נשארו צעד אחד משם. ההגבלה האמיתית
# אינה המספר הזה אלא תקציב הזמן - וזה כבר מטופל ב-Continuation, שמעביר
# את המשך המסע לבקשה הבאה במקום לדחוס הכול לאחת.
MAX_HOPS = int(os.environ.get("EPC_MAX_HOPS", 4))


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
    "image_url": "כתובת תצלום של החלק עצמו מהעמוד, או ריק",
    "diagram_url": "כתובת תרשים הפיצוץ - הסכמה שבה החלק מסומן במקומו, או ריק",
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
- תרשים פיצוץ הוא הסכמה של קבוצת החלקים, עם מספרי מיקום - לא תצלום
  המוצר. בקטלוג יצרן העמוד שבו יושבים המק"טים *הוא* עמוד התרשים,
  ולכן זו לרוב התמונה הגדולה שבראשו. אם יש רק אחד מהשניים, מלא אותו
  והשאר את השני ריק.
- עד 5 חלקים.
{narrow}"""


class EpcVinSource(CatalogSource):
    key = "epc"
    name = SOURCE_NAME
    tier = "oem"
    needs_vin = True
    supports_resume = True

    def available(self):
        return bool(URL_TEMPLATE) and parser_available()

    def _follow(self, start_url, vehicle, part_type, get_page, client,
                resume=None, deadline=None, first_hop=0):
        """תבנית אחת: הבאה, פענוח, ואם צריך צעד נוסף.

        מחזיר ``(מה שנמצא, כתובת, האם עמוד כלשהו זיהה את הרכב)``. הרכיב
        השלישי הוא שמבדיל בין "האתר הכיר את הרכב ואין לו את החלק" לבין
        "האתר לא מכיר את הרכב בכלל" - ראה ``lookup``.
        """
        url = start_url
        found = []
        # ממשיכים ממה שכבר נקבע בבקשה קודמת, אם זה המשך של מסע.
        identified = bool(resume is not None and resume.identified)
        for hop in range(first_hop, MAX_HOPS):
            # מתחת לתקציב הזמן של הבקשה עוצרים ומעבירים את ההמשך
            # הלאה. עדיף עוד סיבוב מהדפדפן מאשר עובד שנהרג באמצע.
            if hop > first_hop and deadline is not None and time.monotonic() > deadline:
                if resume is not None:
                    resume.url, resume.hop = url, hop
                if resume is not None:
                    resume.identified = identified
                trace.note(
                    f"  ⏸ תקציב הבקשה נגמר אחרי {hop - first_hop} צעדים. "
                    f"ממשיכים מ-{url} בבקשה הבאה."
                )
                return found, url, identified
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
            identified = identified or bool(payload.get("vehicle_confirmed"))
            next_url = str(payload.get("next_url") or "").strip()
            # שלוש התשובות האלה הן ההבדל בין "האתר לא מכיר את הרכב",
            # "הגענו לדף הנכון והחלק לא שם" ו"זה דף ביניים": בלעדיהן
            # כל שלושתן נראות על המסך כ'לא החזיר מק"ט'.
            confirmed = bool(payload.get("vehicle_confirmed"))
            trace.note(
                f'    תוצאת הפענוח: {len(found)} מק"טים · '
                f"הרכב אושר בדף: {'כן' if confirmed else 'לא'} · "
                f"המשך מוצע: {next_url or '—'}"
            )
            for raw in found:
                trace.note(
                    f"      · {raw.get('oe_number') or '?'} "
                    f"[{raw.get('confidence') or 'low'}] {raw.get('name') or ''}"
                )
                # ההסבר של המודל הוא הראיה הישירה ביותר למה הוא בחר
                # מה שבחר, והוא נזרק עד היום. כשמק"ט נפסל אחר כך
                # באימות, השורה הזו היא ההסבר.
                if raw.get("note"):
                    trace.note(f"        “{raw['note']}”")
            # אין מק"טים? מה שהמודל *כן* אמר הוא הראיה היחידה. הוא
            # לרוב מסביר בעצמו - "העמוד מציג רשימת קבוצות ולא חלקים",
            # "החלק לא מופיע ברכב הזה" - וזה בדיוק ההבדל שמחפשים.
            if not found:
                explain = str(payload.get("note") or payload.get("reason") or "").strip()
                if explain:
                    trace.note(f"    הסבר המודל: “{explain}”")
                trace.stage(
                    "איתור מק\"ט בדף", False,
                    explain or "המודל לא מצא מק\"ט בעמוד הזה",
                    "" if next_url else
                    "אין קישור להמשיך אליו - ייתכן שזה לא עמוד החלקים.",
                )
            else:
                trace.stage("איתור מק\"ט בדף", True,
                            f'{len(found)} מק"טים מהעמוד')
            trace.stage("זיהוי הרכב בדף", confirmed,
                        "העמוד מאשר שזה הרכב" if confirmed
                        else "אף עמוד לא אישר שזה הרכב",
                        "" if confirmed else
                        "ייתכן שהקטלוג אינו מכסה את היצרן הזה.")
            if found:
                break
            # הצעד הזה ידוע מראש: אם הגענו לעמוד רכב של קטלוג טויוטה,
            # מספר הקבוצה של החלק המבוקש אינו צריך קריאת מודל כדי
            # להתגלות - הוא תקן. קופצים ישר לתרשים ומדלגים על שני
            # צעדי הביניים. כשאין תבנית או אין קבוצה לסוג הזה,
            # ``diagram_url`` מחזיר ``None`` והמסע ממשיך כרגיל.
            shortcut = toyota_groups.diagram_url(next_url or url, part_type)
            if shortcut and shortcut != url:
                trace.note(f"  ⤳ קפיצה ישירה לתרשים הקבוצה: {shortcut}")
                url = shortcut
                continue
            if not next_url or next_url == url:
                trace.note("    אין המשך לעקוב אחריו - עוצרים כאן.")
                break
            url = next_url
        else:
            # הלולאה מוצתה בלי break, כלומר נגמרו הצעדים והיה עוד לאן
            # ללכת. זה כשל שקט: המסע היה בדרך הנכונה ופשוט נקטע.
            if not found:
                trace.note(f"  ⚠ נגמרו {MAX_HOPS} הצעדים, והיה עוד להמשיך אל: {url}")
                trace.stage(
                    "עומק המסע", False,
                    f"נגמרו {MAX_HOPS} הצעדים לפני שהגענו למק\"טים",
                    f"העלה את EPC_MAX_HOPS (כרגע {MAX_HOPS}).",
                )
        if resume is not None:
            resume.clear()
        return found, url, identified

    def lookup(self, vehicle, part_type, oem_numbers=(), fetcher=None, client=None,
               resume=None, deadline=None):
        vin = (vehicle.get("vin") or "").strip()
        if not vin:
            return []
        get_page = fetcher or default_fetcher()
        # המשך של בקשה קודמת: מרימים מאותה כתובת, ולא מתחילים מחדש
        # מעמוד הרכב. בלי זה כל בקשה הייתה חוזרת על הצעדים שכבר שולמו.
        if resume is not None and resume.url:
            trace.note(
                f"{self.name}: ממשיכים מצעד {resume.hop + 1} · {resume.url}"
            )
            start, hop = resume.url, resume.hop
            found, source_url, identified = self._follow(
                start, vehicle, part_type, get_page, client,
                resume=resume, deadline=deadline, first_hop=hop,
            )
            # אותה בדיקה כמו במסלול הרגיל, ולא במקרה: מסע ארוך נפרס
            # על כמה בקשות, ובקטלוג אמיתי זה הרוב. בדיקה שחלה רק על
            # מסע שהסתיים בבקשה אחת הייתה מפספסת בדיוק את המקרים
            # שבגללם היא נכתבה.
            self._require_identification(vehicle, found, identified, resume)
            return self._candidates(found, vehicle, source_url)
        urls = build_urls(vin)
        trace.note(
            f'{self.name}: שלדה {vin} · חלק "{type_name(part_type)}" · '
            f"{len(urls)} תבניות · עד {MAX_HOPS} צעדים לכל אחת · "
            f"הבאה: {fetcher_name(get_page)}"
        )
        if not urls:
            raise FetchError("לא הוגדרה כתובת קטלוג (EPC_VIN_URL).")
        # תבנית שאינה נושאת את השלדה אינה יכולה להחזיר תשובה *לרכב הזה*,
        # וזו שגיאת הגדרה שכדאי לראות לפני שמאשימים את האתר.
        for template in templates():
            if "{vin}" not in template and "{VIN}" not in template:
                trace.note(f"⚠ תבנית בלי {{vin}} - לא תוכל לזהות רכב: {template}")

        found, source_url, failure, answered = [], urls[0], None, False
        identified = False
        for index, start in enumerate(urls, 1):
            trace.note(f"— תבנית {index}/{len(urls)}: {start} —")
            try:
                found, source_url, confirmed = self._follow(
                    start, vehicle, part_type, get_page, client,
                    resume=resume, deadline=deadline,
                )
            except FetchError as exc:
                failure = failure or exc
                trace.note(f"  התבנית הזו לא עבדה: {exc}")
                continue
            answered = True
            # מספיקה תבנית אחת שזיהתה את הרכב כדי שהשליפה כולה תיחשב
            # כמענה - אתר אחד שמכיר אותו הוא כל מה שצריך.
            identified = identified or confirmed
            if found:
                break
        # *כל* התבניות נפלו: זו תקלה, לא "לא נמצא". ההבחנה הזו היא מה
        # שמונע מ"האתר לא ענה" להישמר במטמון כתשובה שלילית לחודשיים.
        # תבנית אחת שענתה "אין כאן כזה חלק" היא תשובה, וגוברת על אחות
        # שנפלה - אחרת תבנית שבורה ברשימה הייתה מסתירה אותה.
        if not answered and failure is not None:
            raise failure
        if answered:
            self._require_identification(vehicle, found, identified, resume)
        return self._candidates(found, vehicle, source_url)

    def _require_identification(self, vehicle, found, identified, resume):
        """אתר שלא זיהה את הרכב לא אמר "אין כאן כזה חלק".

        הוא אמר "אני לא מכיר את הרכב הזה", ואלה שתי תשובות הפוכות.
        בלי ההבחנה, קטלוג שמכסה יצרנים אחרים מהצי מחזיר תשובה ריקה
        לכל רכב שאינו שלו - ``_finish`` שומר אותה במטמון ל-60 יום,
        וכל מכונאי שישאל את אותה שאלה יקבל "אין מק"ט כזה" על רכב
        שהאתר מעולם לא הכיר. זה בדיוק הכישלון השקט שהכתובת השגויה
        יצרה, רק שהפעם הוא נכנס דרך *בחירת האתר*.

        מסע שנעצר בתקציב הזמן אינו מסקנה - הוא באמצע, וייתכן שהעמוד
        שיזהה את הרכב עוד לפניו. ``resume.url`` מסומן בדיוק אז.

        המחיר: שליפה חוזרת כשהמודל לא הצליח לאשר את הרכב מהדף. זה
        הכיוון הנכון לטעות בו - שליפה עולה פעם אחת, תשובה שגויה
        במטמון עולה חודשיים.
        """
        if found or identified:
            return
        if resume is not None and resume.url:
            return
        raise FetchError(
            "האתר ענה, אבל אף עמוד לא זיהה את הרכב הזה - כלומר הוא אינו "
            "מכסה אותו, ולא שהחלק חסר לו. בדוק שהקטלוג שב-EPC_VIN_URL "
            f"מכיל את {_brand(vehicle.get('make')) or 'היצרן'}."
        )

    def _candidates(self, found, vehicle, source_url):
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
                    # תרשים הפיצוץ. הוא היה מבוקש עד היום רק מ-laximo,
                    # שאינו רץ - ולכן הפיצ'ר היה מחובר למקור מת. דווקא
                    # כאן הוא זמין כמעט תמיד: המסע בקטלוג יצרן נגמר
                    # בעמוד התרשים, כי שם יושבים המק"טים.
                    diagram_url=str(raw.get("diagram_url") or "").strip()[:500],
                    source_url=source_url[:500],
                    source_key=self.key,
                    variant_key=str(raw.get("variant") or "").strip()[:80],
                    confidence=str(raw.get("confidence") or "low").lower(),
                    note=str(raw.get("note") or "").strip()[:300],
                    extra={"name": str(raw.get("name") or "").strip()[:200]},
                )
            )
        return candidates
