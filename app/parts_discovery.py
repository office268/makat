"""גילוי מק"טים מהאינטרנט, באמצעות Claude עם חיפוש רשת.

למה דרך מודל ולא גרידה ישירה: קטלוג מקוון מציג בעמוד של דגם גם חלקים
שאינם שלו. בעמוד הקורולה נמצא מסנן שהתיאור שלו אומר CHERY AMULET,
ובעמוד האוקטביה מסנן עם מספר OE של מרצדס. גרידה תמימה מכניסה את שניהם.
המודל קורא את הדף ומחזיר רק את מה שהוא מזהה כשייך לרכב.

התוצאות נכנסות לקטלוג ישירות, ולכן האימות כאן הוא ההגנה היחידה:
כל מועמד עובר בדיקת מבנה, סוג חלק מוכר, ופסילה אם מוזכר בו יצרן רכב
אחר מזה שביקשנו. כל מק"ט שנוסף מסומן ב-notes כמקורו, כדי שניתן יהיה
לאתר ולמחוק את כל מה שהגיע מכאן.
"""
import json
import os
import re

from .taxonomy import PART_TYPES, type_name

DISCOVERY_MODEL = os.environ.get("DISCOVERY_MODEL", "claude-opus-5")
# גרסת כלי חיפוש הרשת של Anthropic. ניתנת להחלפה בלי פריסה, כי היא
# משתנה מעת לעת והשם השגוי נכשל בזמן הקריאה ולא בזמן העלייה.
WEB_SEARCH_TOOL = os.environ.get("WEB_SEARCH_TOOL", "web_search_20250305")
MAX_SEARCHES = int(os.environ.get("DISCOVERY_MAX_SEARCHES", 6))

SOURCE_NOTE = "נוסף בחיפוש אינטרנט אוטומטי (Claude). מקור לא רשמי."
# תת-מחרוזת יציבה של ההערה, לאיתור כל מה שהגיע מכאן גם אם הנוסח ישתנה
SOURCE_MARK = "נוסף בחיפוש אינטרנט אוטומטי"

# סימון המקור של הקטלוג הבסיסי, זה שנאסף לפני שצנרת הגילוי נכתבה.
# משמש להבחין בין מק"ט שהחיפוש האוטומטי הביא לבין מק"ט שהיה כאן לפניו.
CATALOG_MARK = "מקור: קטלוג מקוון"

# יצרני רכב מוכרים, לזיהוי מועמד ששייך לרכב אחר
KNOWN_MARQUES = {
    "toyota", "lexus", "honda", "mazda", "nissan", "mitsubishi", "suzuki",
    "subaru", "hyundai", "kia", "ssangyong", "chery", "geely", "byd", "mg",
    "vw", "volkswagen", "audi", "skoda", "seat", "porsche", "bmw", "mini",
    "mercedes", "smart", "opel", "ford", "chevrolet", "cadillac", "jeep",
    "chrysler", "dodge", "peugeot", "citroen", "citroën", "renault", "dacia",
    "fiat", "alfa", "lancia", "volvo", "jaguar", "land rover", "tesla",
}

HEBREW_TO_MARQUE = {
    "טויוטה": "toyota", "לקסוס": "lexus", "הונדה": "honda", "מאזדה": "mazda",
    "ניסאן": "nissan", "מיצובישי": "mitsubishi", "סוזוקי": "suzuki",
    "סובארו": "subaru", "יונדאי": "hyundai", "קיה": "kia", "פיג'ו": "peugeot",
    "סיטרואן": "citroen", "רנו": "renault", "סקודה": "skoda", "סיאט": "seat",
    "פולקסווגן": "volkswagen", "אאודי": "audi", "אודי": "audi", "פורד": "ford",
    "אופל": "opel", "שברולט": "chevrolet", "מרצדס": "mercedes", "וולוו": "volvo",
    "מאזדה3": "mazda",
}


def discovery_available():
    """האם יש SDK ומפתח. מקביל ל-vision_available."""
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return bool(
        os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    )


def fitment_make(make):
    """שם היצרן כפי שהתאמה חייבת לשמור אותו.

    החיפוש לפי מספר רישוי משווה מול המילה הראשונה של שם היצרן במאגר
    משרד התחבורה ("פיג'ו צרפת" -> "פיג'ו"). שמירת השם המלא יוצרת מק"ט
    שנמצא בקטלוג אבל לא נמצא לעולם בחיפוש - כשל שקט.
    """
    parts = (make or "").strip().split()
    return parts[0] if parts else ""


def marque_of(make):
    """שם היצרן בלועזית, מהעברית או כמו שהוא."""
    key = (make or "").strip()
    return HEBREW_TO_MARQUE.get(key) or HEBREW_TO_MARQUE.get(
        fitment_make(key), fitment_make(key).lower()
    )


def build_prompt(make, model, part_type):
    """ההנחיה למודל. מבקשת JSON בלבד, ומפרטת מה לפסול."""
    return f"""חפש באינטרנט מק"טים אמיתיים של {type_name(part_type)} לרכב:
יצרן: {make} ({marque_of(make)})
דגם: {model}

החזר JSON בלבד, בלי טקסט נוסף, במבנה:
{{"parts": [
  {{"part_number": "מק\\"ט היצרן", "manufacturer": "שם יצרן החלק",
    "oe_number": "מספר OE מקורי או ריק", "oe_brand": "יצרן ה-OE או ריק",
    "price_eur": מספר או null, "source_url": "הכתובת שממנה נלקח",
    "confidence": "high" או "low", "note": "הערה קצרה"}}
]}}

כללים מחייבים:
- רק חלקים שאתה מזהה כמתאימים ל{make} {model}. קטלוג מקוון מציג בעמוד
  של דגם גם חלקים של רכבים אחרים - אל תכלול אותם.
- אם מספר ה-OE שייך ליצרן רכב אחר, אל תכלול את החלק.
- אם אינך בטוח שהחלק מתאים לדגם, סמן confidence: "low".
- אל תמציא מק"ט. אם לא מצאת, החזר רשימה ריקה.
- עד 8 חלפים.
"""


def _json_from(text):
    """מחלץ את אובייקט ה-JSON מהתשובה, גם אם עטוף בטקסט או ב-fence."""
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:index + 1])
                except ValueError:
                    return None
    return None


def _mentions_other_marque(text, wanted):
    """האם בטקסט מוזכר יצרן רכב אחר מזה שביקשנו.

    זה השומר שתופס את המקרה שראינו בפועל: חלק של CHERY שהופיע
    בעמוד של קורולה, והתיאור שלו הסגיר את זה.
    """
    low = (text or "").lower()
    for marque in KNOWN_MARQUES:
        if marque == wanted:
            continue
        if re.search(rf"(?<![a-z]){re.escape(marque)}(?![a-z])", low):
            return marque
    return None


def validate(candidates, make, model, part_type):
    """מחזיר (מאושרים, [(מק"ט, סיבת פסילה)]).

    בלי סקירה אנושית זו ההגנה היחידה, ולכן היא מחמירה: ספק נפסל.
    """
    wanted = marque_of(make)
    accepted, rejected = [], []
    seen = set()

    for raw in candidates or []:
        if not isinstance(raw, dict):
            rejected.append(("?", "רשומה שאינה אובייקט"))
            continue
        number = str(raw.get("part_number") or "").strip()
        maker = str(raw.get("manufacturer") or "").strip()
        note = str(raw.get("note") or "")

        if not number:
            rejected.append(("?", 'חסר מק"ט'))
        elif len(number) > 80:
            rejected.append((number[:40], 'מק"ט ארוך מדי'))
        elif not maker:
            rejected.append((number, "חסר יצרן חלק"))
        elif part_type not in PART_TYPES:
            rejected.append((number, f"סוג חלק לא מוכר: {part_type}"))
        elif str(raw.get("confidence") or "").lower() != "high":
            rejected.append((number, "המודל לא היה בטוח בהתאמה"))
        elif number.lower() in seen:
            rejected.append((number, "כפול בתשובה"))
        else:
            other = _mentions_other_marque(f"{note} {maker}", wanted)
            if other:
                rejected.append((number, f"מוזכר בו יצרן רכב אחר: {other}"))
                continue
            oe_brand = str(raw.get("oe_brand") or "")
            other = _mentions_other_marque(oe_brand, wanted)
            if other:
                rejected.append((number, f"מספר OE של יצרן אחר: {other}"))
                continue
            seen.add(number.lower())
            accepted.append({
                "part_number": number,
                "manufacturer": maker,
                "part_type": part_type,
                "oe_number": str(raw.get("oe_number") or "").strip(),
                "oe_brand": oe_brand.strip(),
                "price_eur": raw.get("price_eur"),
                "source_url": str(raw.get("source_url") or "").strip()[:500],
                "make": make,
                "model": model,
                # שדות שהשליפה החיה מוסיפה (app/catalog_sources). הגילוי
                # מ-/admin/discovery לא ממלא אותם, והם נשארים ריקים.
                "image_url": str(raw.get("image_url") or "").strip()[:500],
                "variant_key": str(raw.get("variant_key") or "").strip()[:80],
                "tier": str(raw.get("tier") or "").strip(),
                "source_key": str(raw.get("source_key") or "").strip(),
                "name": str((raw.get("extra") or {}).get("name") or "").strip()[:200],
            })
    return accepted, rejected


def search(make, model, part_type, client=None):
    """שואל את Claude ומחזיר מועמדים גולמיים. מרים חריגה בכשל."""
    if client is None:
        import anthropic

        client = anthropic.Anthropic()

    response = client.messages.create(
        model=DISCOVERY_MODEL,
        max_tokens=4000,
        tools=[{
            "type": WEB_SEARCH_TOOL,
            "name": "web_search",
            "max_uses": MAX_SEARCHES,
        }],
        messages=[{"role": "user", "content": build_prompt(make, model, part_type)}],
    )
    text = "".join(
        block.text for block in response.content if getattr(block, "type", "") == "text"
    )
    payload = _json_from(text)
    if payload is None:
        raise ValueError("התשובה מהמודל אינה JSON תקין")
    return payload.get("parts") or []


# --------------------------------------------------------------------------
# עבודת גילוי: מטרה אחת (דגם × סוג חלק) לכל בקשה
# --------------------------------------------------------------------------
from datetime import datetime, timezone  # noqa: E402

from .models import Part, db  # noqa: E402
from .services import get_or_create_manufacturer, get_or_create_category  # noqa: E402
from .models import CrossReference, Fitment  # noqa: E402

CATEGORY_OF = {
    "oil_filter": "מסננים", "air_filter": "מסננים", "cabin_filter": "מסננים",
    "fuel_filter": "מסננים",
}


def _now():
    return datetime.now(timezone.utc)


# שדה ריק = מדגם קטן והגיוני, לא סריקה של כל המאגר. בלי התקרות האלה
# "הכל ריק" היה 27,948 דגמים כפול 42 סוגים - מיליון קריאות בתשלום.
DEFAULT_MAKES = int(os.environ.get("DISCOVERY_DEFAULT_MAKES", 2))
DEFAULT_MODELS = int(os.environ.get("DISCOVERY_DEFAULT_MODELS", 2))
MAX_TARGETS = int(os.environ.get("DISCOVERY_MAX_TARGETS", 40))
# כמה דגמים מראש דירוג הפערים נכנסים לתכנון. MAX_TARGETS חותך ממילא,
# אבל דירוג של אלפי דגמים בכל תצוגה מקדימה הוא עבודה מיותרת.
DEFAULT_GAP_MODELS = int(os.environ.get("DISCOVERY_GAP_MODELS", 8))

# החלפים שמוסך באמת מחליף, כשלא נבחר סוג
DEFAULT_PART_TYPES = [
    "oil_filter", "air_filter", "cabin_filter",
    "fuel_filter", "wiper_blade", "brake_pads_front",
]


def gap_pairs(make=None, limit=None):
    """הדגמים עם הפער הגדול ביותר בין הרכבים שעל הכביש למק"טים שלנו.

    זה מקור המטרות המועדף: לחפש מק"טים לדגם שיש לו מאתיים מק"טים
    ושלושים אלף רכבים הוא בזבוז, ולדגם עם חמישים אלף רכבים ואפס
    מק"טים זו בדיוק העבודה.

    מוחזרות זוגות בכתיב הקטלוג, כדי שההתאמות שייווצרו יישבו לצד
    הקיימות ולא יפתחו כתיב שני לאותו יצרן.
    """
    from . import fleet_stats
    from .services import catalog_make

    ranked, _ = fleet_stats.gap_ranking(make=make, limit=limit or DEFAULT_GAP_MODELS)
    return [(catalog_make(row.search_make), row.model) for row in ranked]


PLAN_SOURCES = {
    "manual": "לפי מה שהוקלד",
    "gap": "לפי דירוג הפערים בצי",
    "variants": "לפי מספר הווריאנטים - עדיין אין ספירת צי",
}


def plan_source(make=None, model=None):
    """מאיפה יילקחו המטרות, כדי שהמסך יגיד את זה במקום שהמשתמש ינחש.

    בדיקה זולה בכוונה: קיומו של צילום צי, ולא הדירוג עצמו. תצוגה
    מקדימה שרצה על כל הקלדה לא צריכה לשלם על דירוג מלא.
    """
    from . import fleet_stats

    if model or (make and model):
        return "manual"
    return "gap" if fleet_stats.summary()["models"] else "variants"


def plan_targets(make=None, model=None, part_types=None):
    """מה ירוץ בפועל. מחזיר (מטרות, האם נחתך בתקרה).

    שדה ריק מתמלא מדירוג הפערים: כמה רכבים בטווח הקנייה יש לדגם, חלקי
    המק"טים שכבר יש לנו עבורו. פעם זה נעשה לפי מספר הווריאנטים בקטלוג
    הדגמים - הפרוקסי היחיד שהיה - וכיום יש ספירה אמיתית של הצי, כך
    שהגילוי מכוון לשוק הגדול שאינו מכוסה במקום לדגם שבמקרה יש לו הרבה
    קודי דגם.

    בלי צילום צי (עוד לא נספר) חוזרים לפרוקסי הישן, כי גילוי שמכוון
    פחות טוב עדיף על מסך שלא עושה כלום.

    התוצאה נחתכת ב-MAX_TARGETS כדי שלחיצה אחת לא תייצר חשבון בלתי צפוי.
    """
    from .vehicle_catalog import popular_makes, popular_models

    make = (make or "").strip() or None
    model = (model or "").strip() or None
    types = [t for t in (part_types or []) if t in PART_TYPES] or DEFAULT_PART_TYPES

    if make and model:
        pairs = [(make, model)]
    elif model:
        pairs = []  # דגם בלי יצרן - לא ניתן להתאמה חד-משמעית
    else:
        pairs = gap_pairs(make=make)
        if not pairs:
            pairs = (
                popular_models(make, limit=DEFAULT_MODELS)
                if make
                else [
                    pair
                    for candidate in popular_makes(limit=DEFAULT_MAKES)
                    for pair in popular_models(candidate, limit=DEFAULT_MODELS)
                ]
            )

    targets = [[mk, md, t] for mk, md in pairs for t in types]
    return targets[:MAX_TARGETS], len(targets) > MAX_TARGETS


class DiscoveryJob(db.Model):
    """הרצת גילוי אחת. המטרות בתור, אחת לכל בקשה.

    קריאה למודל עם חיפוש רשת יכולה לקחת עשרות שניות, ו-gunicorn הורג
    בקשה אחרי 60. לכן אותו דפוס כמו ייבוא דגמי הרכב: הדפדפן מבקש מטרה
    אחת בכל פעם, וההתקדמות יושבת ב-DB כדי ששני ה-workers יראו אותה.
    """

    __tablename__ = "discovery_jobs"

    RUNNING, DONE, FAILED, CANCELLED = "running", "done", "failed", "cancelled"
    STATUS_LABELS = {RUNNING: "בתהליך", DONE: "הושלם",
                     FAILED: "נכשל", CANCELLED: "בוטל"}

    id = db.Column(db.Integer, primary_key=True)
    status = db.Column(db.String(20), default=RUNNING, nullable=False, index=True)
    targets = db.Column(db.Text, nullable=False)     # JSON: [[make, model, type], ...]
    cursor = db.Column(db.Integer, default=0, nullable=False)
    created = db.Column(db.Integer, default=0, nullable=False)
    updated = db.Column(db.Integer, default=0, nullable=False)
    rejected = db.Column(db.Integer, default=0, nullable=False)
    log = db.Column(db.Text, default="")             # מה נוסף ומה נפסל, ולמה
    error = db.Column(db.Text)
    started_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    started_at = db.Column(db.DateTime, default=_now)
    updated_at = db.Column(db.DateTime, default=_now)
    finished_at = db.Column(db.DateTime)

    @property
    def target_list(self):
        try:
            return json.loads(self.targets) or []
        except ValueError:
            return []

    @property
    def total(self):
        return len(self.target_list)

    @property
    def is_running(self):
        return self.status == self.RUNNING

    @property
    def progress_pct(self):
        return min(100, round(self.cursor * 100 / self.total)) if self.total else 0

    @property
    def status_label(self):
        return self.STATUS_LABELS.get(self.status, self.status)

    def to_dict(self):
        return {
            "id": self.id, "status": self.status, "status_label": self.status_label,
            "cursor": self.cursor, "total": self.total, "created": self.created,
            "updated": self.updated, "rejected": self.rejected,
            "progress_pct": self.progress_pct, "is_running": self.is_running,
            "error": self.error, "log": (self.log or "").strip().split("\n")[-40:],
        }


def active_job():
    return (
        DiscoveryJob.query.filter_by(status=DiscoveryJob.RUNNING)
        .order_by(DiscoveryJob.id.desc()).first()
    )


def latest_job():
    return DiscoveryJob.query.order_by(DiscoveryJob.id.desc()).first()


def start_job(targets, user_id=None):
    """פותח הרצה. מטרה = (יצרן, דגם, סוג חלק)."""
    existing = active_job()
    if existing is not None:
        return existing
    job = DiscoveryJob(
        targets=json.dumps(targets, ensure_ascii=False),
        started_by_id=user_id,
        log="",
    )
    db.session.add(job)
    db.session.commit()
    return job


def cancel_job(job):
    if job is not None and job.is_running:
        job.status = DiscoveryJob.CANCELLED
        job.finished_at = _now()
        db.session.commit()
    return job


def save(accepted, source_note=None, source_mark=None):
    """כותב מועמדים מאושרים לקטלוג. מחזיר (נוספו, עודכנו).

    ``source_note``/``source_mark`` מאפשרים לשליפה החיה לסמן את מה
    שהיא הכניסה בסימון משלה, כדי שסקירת הגילוי לא תציג את שתי הצנרות
    כאותו דבר - ומחיקה של אחת לא תיקח איתה את השנייה.
    """
    note_text = source_note or SOURCE_NOTE
    mark = source_mark or SOURCE_MARK
    created = updated = 0
    for row in accepted:
        part = Part.query.filter_by(part_number=row["part_number"]).first()
        is_new = part is None
        if is_new:
            part = Part(part_number=row["part_number"])
            db.session.add(part)
            part.name_he = row.get("name") or f'{type_name(row["part_type"])} {row["model"]}'
            part.part_type = row["part_type"]
            part.category = get_or_create_category(
                CATEGORY_OF.get(row["part_type"], "כללי")
            )
        part.manufacturer = get_or_create_manufacturer(row["manufacturer"])
        # תמונה נכתבת רק כשאין - מק"ט שכבר קיבל תמונה מייבוא או מעריכה
        # ידנית לא נדרס על ידי מה שנמצא בעמוד חיפוש.
        if row.get("image_url") and not part.image_url:
            part.image_url = row["image_url"]
        # מק"ט קיים שומר את ההערה שלו. דריסה הייתה מוחקת את סימון
        # המקור הקודם ומציגה חלף שנאסף קודם כאילו הגיע מהחיפוש.
        source = f'{note_text} {row.get("source_url") or ""}'.strip()
        if is_new:
            part.notes = source
        elif mark not in (part.notes or ""):
            part.notes = f'{part.notes or ""} | {source}'.strip(" |")

        # התאמה חדשה מתווספת; קיימת לא משוכפלת
        wanted_make = fitment_make(row["make"])
        exists = any(
            (f.make or "") == wanted_make and (f.model or "") == row["model"]
            for f in part.fitments
        )
        if not exists:
            # השליפה החיה יודעת גם מנוע, שנה ווריאנט - וזה ההבדל בין
            # התאמה שתימצא לרכב הנכון לבין התאמה לכל הדגם.
            part.fitments.append(
                Fitment(
                    make=fitment_make(row["make"]),
                    model=row["model"],
                    engine_code=row.get("engine_code") or None,
                    year_from=row.get("year"),
                    year_to=row.get("year"),
                    variant_key=row.get("variant_key") or None,
                )
            )
        if row.get("oe_number") and not any(
            r.ref_number == row["oe_number"] for r in part.cross_refs
        ):
            part.cross_refs.append(
                CrossReference(
                    ref_type="OEM", ref_number=row["oe_number"],
                    ref_brand=row.get("oe_brand") or None,
                )
            )
        created += 1 if is_new else 0
        updated += 0 if is_new else 1
    db.session.commit()
    return created, updated


def run_step(job, searcher=None):
    """מטרה אחת: חיפוש, אימות, כתיבה. מחזיר את העבודה."""
    if not job.is_running:
        return job
    targets = job.target_list
    if job.cursor >= len(targets):
        job.status = DiscoveryJob.DONE
        job.finished_at = _now()
        db.session.commit()
        return job

    make, model, part_type = targets[job.cursor]
    lines = []
    try:
        raw = (searcher or search)(make, model, part_type)
    except Exception as exc:  # רשת, מפתח, מכסה או תשובה פגומה
        job.error = f"{make} {model} · {type_name(part_type)}: {exc}"
        job.cursor += 1
        job.updated_at = _now()
        db.session.commit()
        return job

    accepted, rejected = validate(raw, make, model, part_type)
    created, updated = save(accepted)
    job.created += created
    job.updated += updated
    job.rejected += len(rejected)

    header = f"{make} {model} · {type_name(part_type)}"
    lines.append(f"{header}: נוספו {created}, עודכנו {updated}, נפסלו {len(rejected)}")
    for number, reason in rejected[:5]:
        lines.append(f"    ✗ {number} — {reason}")

    job.log = ((job.log or "") + "\n".join(lines) + "\n")[-8000:]
    job.error = None
    job.cursor += 1
    job.updated_at = _now()
    if job.cursor >= len(targets):
        job.status = DiscoveryJob.DONE
        job.finished_at = _now()
    db.session.commit()
    return job


# --------------------------------------------------------------------------
# סקירה: מה נכנס לקטלוג, ומה נראה חשוד
# --------------------------------------------------------------------------

def discovered_parts():
    """כל מק"ט שהחיפוש האוטומטי הכניס או נגע בו, החדש קודם."""
    return (
        Part.query.filter(Part.notes.like(f"%{SOURCE_MARK}%"))
        .order_by(Part.id.desc())
        .all()
    )


def source_url_of(part):
    """הכתובת שהמודל דיווח עליה, מתוך ההערה."""
    for chunk in (part.notes or "").split():
        if chunk.startswith("http"):
            return chunk
    return None


def _flag(text, level="suspect", structural=False):
    """דגל אחד.

    level="suspect" הוא סיבה למחוק, ולכן השורה נבחרת מראש.
    level="caution" הוא בדיוק ההפך - סיבה להיזהר לפני מחיקה.

    structural=True מסמן ליקוי שאימות מול הרשת לא יכול לפתור: גם אם
    המק"ט אמיתי לגמרי, חלף בלי התאמה לרכב לא יימצא בחיפוש לפי רישוי.
    """
    return {"text": text, "level": level, "structural": structural}


def review_flags(part):
    """מה חשוד במק"ט הזה. רשימה ריקה = לא נמצא שום דבר לתפוס.

    כל הבדיקות כאן חינמיות ומקומיות. הן לא מחליפות שיפוט אנושי -
    הן מסמנות את מה שאפשר לתפוס בלי לצאת לרשת.
    """
    flags = []
    if not part.fitments:
        flags.append(_flag("בלי התאמה לרכב - לא יימצא בחיפוש לפי מספר רישוי",
                           structural=True))
    if part.manufacturer is None:
        flags.append(_flag("בלי יצרן חלק", structural=True))
    if part.part_type not in PART_TYPES:
        flags.append(_flag(f"סוג חלק לא מוכר: {part.part_type or '—'}",
                           structural=True))

    # אותו שומר של הגילוי, עכשיו על מה שכבר נשמר בקטלוג
    wanted = {marque_of(fit.make) for fit in part.fitments if fit.make}
    for ref in part.cross_refs:
        text = f"{ref.ref_brand or ''} {ref.ref_number or ''}"
        # מוזכר יצרן רכב כלשהו שאינו אחד מאלה שהחלף מותאם להם
        other = _mentions_other_marque(text, None)
        if other and other not in wanted:
            flags.append(_flag(f'מק"ט מקביל של יצרן אחר: {other}'))

    if CATALOG_MARK in (part.notes or ""):
        flags.append(_flag("היה בקטלוג לפני החיפוש - מחיקה תסיר גם עבודה ידנית",
                           level="caution"))
    return flags


def suspect(flags):
    """האם יש כאן סיבה למחוק, להבדיל מסיבה להיזהר."""
    return any(flag["level"] == "suspect" for flag in flags)


def structural(flags):
    """האם יש ליקוי שאימות מול הרשת לא יכול לסגור."""
    return any(flag["structural"] for flag in flags)


def build_verify_prompt(part):
    """הנחיית האימות: לא לחפש חלף, אלא לשפוט חלף אחד שכבר נכנס."""
    fits = ", ".join(
        f"{fit.make} {fit.model}".strip() for fit in part.fitments
    ) or "לא נרשמה התאמה"
    refs = ", ".join(ref.ref_number for ref in part.cross_refs) or "אין"
    maker = part.manufacturer.name if part.manufacturer else "לא ידוע"
    return f"""בדוק באינטרנט האם החלף הזה באמת מתאים לרכב שרשום לו:

מק"ט: {part.part_number}
יצרן החלק: {maker}
סוג: {type_name(part.part_type)}
מתאים לפי הקטלוג: {fits}
מק"טים מקוריים שנרשמו: {refs}

החזר JSON בלבד, בלי טקסט נוסף:
{{"verdict": "fits" או "not_fits" או "unsure",
  "reason": "משפט אחד בעברית - למה",
  "source_url": "הכתובת שעליה הסתמכת, או ריק"}}

כללים:
- "not_fits" אם מצאת שהחלף שייך ליצרן רכב אחר, או שהמספר המקורי שייך
  ליצרן אחר, או שהוא לא מתאים לדגם שנרשם.
- "unsure" אם לא מצאת מקור אמין. אל תנחש.
- "fits" רק אם מצאת אישור ממשי להתאמה.
"""


def verify(part, client=None):
    """שואל את המודל על מק"ט אחד. מחזיר dict. מרים חריגה בכשל."""
    if client is None:
        import anthropic

        client = anthropic.Anthropic()

    response = client.messages.create(
        model=DISCOVERY_MODEL,
        max_tokens=1500,
        tools=[{"type": WEB_SEARCH_TOOL, "name": "web_search", "max_uses": MAX_SEARCHES}],
        messages=[{"role": "user", "content": build_verify_prompt(part)}],
    )
    text = "".join(
        block.text for block in response.content if getattr(block, "type", "") == "text"
    )
    payload = _json_from(text)
    if payload is None:
        raise ValueError("התשובה מהמודל אינה JSON תקין")
    verdict = str(payload.get("verdict") or "").lower()
    return {
        "verdict": verdict if verdict in {"fits", "not_fits", "unsure"} else "unsure",
        "reason": str(payload.get("reason") or "").strip()[:300],
        "source_url": str(payload.get("source_url") or "").strip()[:500],
    }
