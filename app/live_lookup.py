"""שליפה חיה: מה קורה כשמבקשים חלק שאין לו מק"ט בקטלוג.

הזרימה במסך הזיהוי נעצרה עד היום ב"אין מק"ט מסוג זה בקטלוג עבור הרכב
הזה". כאן היא ממשיכה: הרכב כבר מזוהה ויש לו מספר שלדה, ואפשר לשאול
את קטלוג היצרן מה המק"ט המקורי לרכב הזה - ואז לשאול קטלוג חלופים מה
מתאים למספר הזה.

שלושה דברים שקובעים את המבנה של הקובץ:

1. **זה איטי.** בקשת רשת לאתר חיצוני ואחריה קריאה למודל, כפול שני
   שלבים. gunicorn הורג בקשה אחרי 60 שניות. לכן אותו דפוס שכבר עובד
   פעמיים בקוד (``DiscoveryJob``, ייבוא דגמי הרכב): עבודה בטבלה,
   שלב אחד לכל בקשה, והדפדפן מסקר.

2. **זה עולה.** תשובה נשמרת ב-``LookupCache`` לפי *דגם*, לא לפי רכב -
   ``vin_key`` הוא שמונת התווים הראשונים של השלדה ותו שנת הדגם, כלומר
   יצרן, דגם ומנוע בלי המספר הסידורי. הקורולה החמישים מאותו דגם לא
   שולחת בקשה לאף אתר, וגם לא מדליפה איזה רכב בדיוק נשאל.

3. **זה נכנס לקטלוג.** כל מק"ט שעובר את האימות של ``parts_discovery``
   נשמר עם התאמה מדויקת (מנוע, שנה, וריאנט) וסימון מקור משלו, כך
   שהחיפוש הבא לאותו דגם נענה מקומית. מה שלא עבר אימות מוצג למשתמש
   ומסומן, אבל לא נכתב.
"""
import json
import os
import time
from datetime import datetime, timedelta, timezone

from flask import current_app
from sqlalchemy.exc import IntegrityError

from . import catalog_sources, parts_discovery, services
from .catalog_sources import trace
from .catalog_sources.base import Continuation
from .catalog_sources.base import PARSE_MODEL, fetcher_kind
from .models import Part, db
from .taxonomy import PART_TYPES, type_name

# סימון המקור של כל מה שהשליפה החיה הכניסה. נפרד מסימון הגילוי
# מ-/admin/discovery, כדי ששתי הצנרות יהיו ניתנות להפרדה ולמחיקה בנפרד.
SOURCE_NOTE = 'נוסף בשליפה חיה לפי מספר שלדה. מקור לא רשמי.'
SOURCE_MARK = "נוסף בשליפה חיה לפי מספר שלדה"

CACHE_DAYS = int(os.environ.get("LOOKUP_CACHE_DAYS", 60))
# תקרת שליפות חיות לארגון ליום. אחת שווה כמה בקשות רשת וכמה קריאות
# מודל, ובלי תקרה לחיצה חוזרת היא חשבון פתוח.
DAILY_LIMIT = int(os.environ.get("LOOKUP_DAILY_LIMIT", 50))

# יומן החקירה. כמה תווים נשמרים בעבודה, וכמה שורות עולות למסך.
# התקרה הקודמת (4,000 תווים, 20 שורות) נקבעה כשהיומן היה שורת סיכום
# לכל מקור; יומן שמתעד כתובות, הפניות ותשובות מודל צריך מקום, אחרת
# הוא נחתך בדיוק במקום שבו מתחילה החקירה.
LOG_CHARS = int(os.environ.get("LOOKUP_LOG_CHARS", 24000))
LOG_LINES = int(os.environ.get("LOOKUP_LOG_LINES", 200))

# תקציב הזמן לבקשת שלב אחת, לפני ש-gunicorn הורג אותה. מקור שמנווט
# בקטלוג עושה כמה הבאות ברצף, וכל אחת היא עשרות שניות; במקום לדחוס
# את כולן לבקשה אחת ולהיהרג באמצע, הן נחתכות כאן וממשיכות בבקשה
# הבאה. המרווח הוא לקריאת המודל שסוגרת את הצעד האחרון.
STEP_BUDGET = float(os.environ.get("LOOKUP_STEP_BUDGET", 0)) or max(
    10.0, float(os.environ.get("WEB_TIMEOUT", 60))
    - float(os.environ.get("CATALOG_MODEL_BUDGET", 15)) - 10.0
)


def _now():
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# מפתח המטמון
# --------------------------------------------------------------------------

def vin_key(vehicle):
    """מזהה *דגם ומנוע*, לא רכב.

    מבנה מספר שלדה: 1-3 יצרן, 4-8 תיאור הדגם, 9 ספרת ביקורת, 10 שנת
    דגם, 11 מפעל, 12-17 מספר סידורי. אנחנו לוקחים 1-8 ואת 10 - כל מה
    שמשפיע על התאמת חלף, ואף תו שמזהה רכב מסוים.

    בלי שלדה נופלים לזהות הרישומית (יצרן/דגם/שנה/מנוע). היא גסה יותר,
    אבל היא מה שיש, והקידומת מבדילה בין השתיים כדי ששתי שיטות מפתוח
    לא יתערבבו במטמון אחד.
    """
    vin = "".join((vehicle.get("vin") or "").split()).upper()
    if len(vin) >= 10:
        return f"vin:{vin[:8]}{vin[9]}"
    parts = [
        (vehicle.get("make") or "").strip(),
        (vehicle.get("model") or "").strip(),
        str(vehicle.get("year") or ""),
        (vehicle.get("engine_code") or "").strip(),
    ]
    return "reg:" + "|".join(parts).upper()


# --------------------------------------------------------------------------
# מטמון התשובות
# --------------------------------------------------------------------------

class LookupCache(db.Model):
    """תשובה אחת לשאלה אחת: (דגם, סוג חלק) -> מה שהמקורות החזירו."""

    __tablename__ = "lookup_cache"
    __table_args__ = (
        db.UniqueConstraint("vin_key", "part_type", name="uq_lookup_cache_key"),
    )

    id = db.Column(db.Integer, primary_key=True)
    vin_key = db.Column(db.String(120), nullable=False, index=True)
    part_type = db.Column(db.String(60), nullable=False, index=True)
    payload = db.Column(db.Text, nullable=False)  # JSON
    sources = db.Column(db.String(120))
    hits = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=_now)
    used_at = db.Column(db.DateTime, default=_now)

    @property
    def data(self):
        try:
            return json.loads(self.payload) or {}
        except ValueError:
            return {}


def cached(vehicle, part_type, max_age_days=None):
    """תשובה שמורה וטרייה, או None. כל פגיעה נספרת ומעדכנת זמן שימוש."""
    row = LookupCache.query.filter_by(
        vin_key=vin_key(vehicle), part_type=part_type
    ).first()
    if row is None:
        return None
    days = CACHE_DAYS if max_age_days is None else max_age_days
    age_limit = _now() - timedelta(days=days)
    created = row.created_at
    if created is not None and created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    if created is not None and created < age_limit:
        return None
    row.hits = (row.hits or 0) + 1
    row.used_at = _now()
    db.session.commit()
    return row.data


def remember(vehicle, part_type, payload, sources=""):
    """שומר תשובה. שאלה שכבר נשאלה נדרסת בתשובה החדשה."""
    key = vin_key(vehicle)
    row = LookupCache.query.filter_by(vin_key=key, part_type=part_type).first()
    if row is None:
        row = LookupCache(vin_key=key, part_type=part_type)
        db.session.add(row)
    row.payload = json.dumps(payload, ensure_ascii=False)
    row.sources = sources[:120]
    row.created_at = _now()
    row.used_at = _now()
    try:
        db.session.commit()
    except IntegrityError:
        # שני workers שסיימו את אותה שאלה באותו רגע. השני מוותר -
        # התשובה כבר במטמון, והיא זהה.
        db.session.rollback()
        return LookupCache.query.filter_by(vin_key=key, part_type=part_type).first()
    return row


def prune_cache(days=None):
    """מוחק תשובות ישנות. מחזיר כמה נמחקו."""
    cutoff = _now() - timedelta(days=CACHE_DAYS if days is None else days)
    rows = LookupCache.query.filter(LookupCache.created_at < cutoff).all()
    for row in rows:
        db.session.delete(row)
    db.session.commit()
    return len(rows)


# --------------------------------------------------------------------------
# העבודה
# --------------------------------------------------------------------------

class LookupJob(db.Model):
    """שליפה חיה אחת. שלב = מקור, שלב אחד לכל בקשת HTTP."""

    __tablename__ = "lookup_jobs"

    RUNNING, DONE, FAILED, CANCELLED = "running", "done", "failed", "cancelled"
    # המשתמש קיבל את מה שחיפש ובחר לא להמשיך למקור הבא. נפרד מ-
    # CANCELLED, שהוא נטישה: כאן יש תשובה, והיא פשוט חלקית בכוונה.
    STOPPED = "stopped"
    STATUS_LABELS = {RUNNING: "מחפש", DONE: "הושלם", FAILED: "נכשל",
                     CANCELLED: "בוטל", STOPPED: "נעצר לבקשתך"}

    id = db.Column(db.Integer, primary_key=True)
    status = db.Column(db.String(20), default=RUNNING, nullable=False, index=True)
    plate = db.Column(db.String(20), index=True)
    vin_key = db.Column(db.String(120), index=True)
    part_type = db.Column(db.String(60), nullable=False)
    vehicle = db.Column(db.Text, nullable=False)   # JSON - צילום הרכב
    stages = db.Column(db.Text, nullable=False)    # JSON - שמות המקורות
    cursor = db.Column(db.Integer, default=0, nullable=False)
    results = db.Column(db.Text, default="")       # JSON - מה נמצא
    saved = db.Column(db.Integer, default=0, nullable=False)
    log = db.Column(db.Text, default="")
    # המשך המסע בתוך המקור הנוכחי, כשהוא לא הספיק בבקשה אחת. כל עוד
    # יש כאן כתובת, ``cursor`` *אינו* מתקדם: אנחנו עדיין באותו מקור.
    resume_url = db.Column(db.String(500), default="")
    resume_hop = db.Column(db.Integer, default=0, nullable=False)
    # השלבים של המקור האחרון שרץ, כ-JSON. היומן אומר *מה קרה*; זה
    # אומר *איפה נעצר*, וזה מה שעולה למסך כפירוט הכישלון.
    diagnosis = db.Column(db.Text, default="")
    error = db.Column(db.Text)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), index=True)
    started_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    started_at = db.Column(db.DateTime, default=_now)
    updated_at = db.Column(db.DateTime, default=_now)
    finished_at = db.Column(db.DateTime)

    @property
    def stage_report(self):
        """השלבים של המקור האחרון, ומי מהם נכשל."""
        try:
            rows = json.loads(self.diagnosis) if self.diagnosis else []
        except ValueError:
            rows = []
        return rows if isinstance(rows, list) else []

    @property
    def failed_stage(self):
        """השלב הראשון שנכשל, או None - "הסיבה" בשורה אחת."""
        for row in self.stage_report:
            if isinstance(row, dict) and not row.get("ok"):
                return row
        return None

    @property
    def stage_list(self):
        try:
            return json.loads(self.stages) or []
        except ValueError:
            return []

    @property
    def result_data(self):
        try:
            return json.loads(self.results) or {"results": [], "unverified": []}
        except ValueError:
            return {"results": [], "unverified": []}

    @property
    def total(self):
        return len(self.stage_list)

    @property
    def is_running(self):
        return self.status == self.RUNNING

    @property
    def progress_pct(self):
        return min(100, round(self.cursor * 100 / self.total)) if self.total else 100

    @property
    def status_label(self):
        return self.STATUS_LABELS.get(self.status, self.status)

    @staticmethod
    def _source_name(key):
        source = catalog_sources.get(key)
        return source.name if source else key

    @property
    def stage_label(self):
        """המקור שהשלב הבא ישאל. נקרא *לפני* שהשלב רץ."""
        stages = self.stage_list
        if self.is_running and self.cursor < len(stages):
            return self._source_name(stages[self.cursor])
        return ""

    @property
    def done_stage_label(self):
        """המקור שזה עתה סיים - מה שהתוצאה שעל המסך הגיעה ממנו."""
        stages = self.stage_list
        if 0 < self.cursor <= len(stages):
            return self._source_name(stages[self.cursor - 1])
        return ""

    @property
    def awaiting_approval(self):
        """מקור סיים, ויש עוד אחריו - כאן נעצרים ושואלים את המשתמש.

        כל מקור הוא בקשת רשת וקריאת מודל, והוא נספר במכסה היומית של
        הארגון. מי שכבר קיבל את המק"ט המקורי לשלדה שלו לא בהכרח רוצה
        שנמשיך לחפש לו חלופות, ולכן ההמשכה היא בחירה ולא ברירת מחדל.
        """
        return self.is_running and 0 < self.cursor < self.total

    def to_dict(self):
        data = self.result_data
        return {
            "id": self.id,
            "status": self.status,
            "status_label": self.status_label,
            "stage_label": self.stage_label,
            "done_stage_label": self.done_stage_label,
            "awaiting_approval": self.awaiting_approval,
            "cursor": self.cursor,
            "total": self.total,
            # ‏cursor לבדו כבר אינו סימן להתקדמות: בזמן שמקור מנווט
            # בקטלוג הוא נשאר במקומו והצעד הוא שזז. הדפדפן משתמש
            # בשניהם יחד כדי לדעת שהשליפה לא נתקעה.
            "hop": self.resume_hop or 0,
            "resuming": bool(self.resume_url),
            "progress_pct": self.progress_pct,
            "is_running": self.is_running,
            "saved": self.saved,
            "error": self.error,
            "part_type": self.part_type,
            "part_type_name": type_name(self.part_type),
            "results": data.get("results") or [],
            "unverified": data.get("unverified") or [],
            "log": (self.log or "").strip().split("\n")[-LOG_LINES:] if self.log else [],
            "diagnosis": self.stage_report,
            "failed_stage": self.failed_stage,
            "from_cache": False,
        }


def available():
    """האם השליפה החיה יכולה לרוץ בכלל - יש מקור מופעל וזמין."""
    return any(source.available() for source in catalog_sources.enabled_sources())


def usable_sources(vehicle):
    """המקורות שרלוונטיים לרכב הזה, לפי הסדר."""
    has_vin = bool((vehicle.get("vin") or "").strip())
    return [
        source
        for source in catalog_sources.enabled_sources()
        if source.available() and (has_vin or not source.needs_vin)
    ]


def quota_left(organization_id):
    """כמה שליפות נשארו לארגון היום."""
    if not DAILY_LIMIT:
        return 0
    since = _now() - timedelta(days=1)
    used = LookupJob.query.filter(
        LookupJob.organization_id == organization_id, LookupJob.started_at >= since
    ).count()
    return max(0, DAILY_LIMIT - used)


def active_job(user_id=None):
    query = LookupJob.query.filter_by(status=LookupJob.RUNNING)
    if user_id is not None:
        query = query.filter_by(started_by_id=user_id)
    return query.order_by(LookupJob.id.desc()).first()


def start_job(vehicle, part_type, user=None):
    """פותח שליפה. מרים ``ValueError`` עם סיבה קריאה כשאי אפשר."""
    if part_type not in PART_TYPES:
        raise ValueError("סוג חלק לא מוכר.")
    sources = usable_sources(vehicle)
    if not sources:
        raise ValueError(
            "אין מקור קטלוגי זמין. צריך מפתח ANTHROPIC_API_KEY, ולחיפוש "
            "לפי שלדה - רכב שיש לו מספר שלדה במרשם."
        )
    organization_id = getattr(user, "organization_id", None)
    if organization_id and not quota_left(organization_id):
        raise ValueError(
            f"נוצלה תקרת השליפות החיות ליממה ({DAILY_LIMIT}). "
            "מה שכבר נשלף נשמר בקטלוג וממשיך לעבוד."
        )
    job = LookupJob(
        plate=vehicle.get("plate"),
        vin_key=vin_key(vehicle),
        part_type=part_type,
        vehicle=json.dumps(vehicle, ensure_ascii=False),
        stages=json.dumps([source.key for source in sources]),
        results=json.dumps({"results": [], "unverified": []}, ensure_ascii=False),
        organization_id=organization_id,
        started_by_id=getattr(user, "id", None),
    )
    # הכותרת של היומן עונה על השאלה הראשונה בכל חקירה - *מה בעצם רץ*.
    # ‏CATALOG_SOURCES ו-CATALOG_FETCHER נקראים בזמן ייבוא, ולכן אין
    # דרך אחרת לדעת מהמסך אם משתנה סביבה שהוגדר אכן נתפס.
    job.log = (
        f"מקורות לפי הסדר: {' ← '.join(source.name for source in sources)}\n"
        f"מסלול הבאה: {fetcher_kind()} · מודל פענוח: {PARSE_MODEL}\n"
        f"רכב: {vehicle.get('make') or '—'} {vehicle.get('model') or ''} "
        f"{vehicle.get('year') or ''} · מנוע {vehicle.get('engine_code') or '—'} · "
        f"שלדה {vehicle.get('vin') or '—'}\n"
    )
    db.session.add(job)
    db.session.commit()
    return job


def cancel_job(job):
    if job is not None and job.is_running:
        job.status = LookupJob.CANCELLED
        job.finished_at = _now()
        db.session.commit()
    return job


def stop_job(job):
    """המשתמש קיבל את מה שחיפש ואינו ממשיך למקור הבא.

    **התשובה החלקית אינה נכנסת למטמון.** היא חלקית בבחירה של מי
    ששאל, ושמירתה הייתה מגישה אותה למכונאי הבא כאילו זה כל מה שיש -
    בלי שהוא בחר בכך ובלי דרך לדעת. מה שכן נשמר הוא מה שנכנס לקטלוג
    תוך כדי (``parts_discovery.save`` רץ לכל מקור בנפרד), וזה נשאר.

    לכן גם אין כאן "המשך אחר כך": העבודה נסגרת, וחיפוש חדש לאותו
    רכב יתחיל מהמקור הראשון. בקשה חוזרת תיענה ממילא מהקטלוג המקומי,
    שכבר מחזיק את מה שהמקור הראשון הביא.
    """
    if job is not None and job.is_running:
        job.status = LookupJob.STOPPED
        job.finished_at = _now()
        job.updated_at = _now()
        db.session.commit()
    return job


def known_oem_numbers(vehicle, part_type, found):
    """מספרי ה-OE שיש לנו כבר, לשלב החלופים.

    קודם מה שהשלב הראשון הוציא מהשלדה, ואחריו מה שכבר יושב בקטלוג
    לרכב הזה. השני הוא הסיבה ששלב החלופים שווה משהו גם כשאין שלדה.
    """
    numbers = [row["oe_number"] for row in found if row.get("oe_number")]
    for part in services.parts_for_vehicle(vehicle, part_type):
        numbers.extend(part.oem_numbers)
    seen, unique = set(), []
    for number in numbers:
        key = number.strip().upper()
        if key and key not in seen:
            seen.add(key)
            unique.append(number.strip())
    return unique


def _result_row(row, source, part_id=None, verified=True, reason=""):
    """שורה אחת כפי שהמסך יקבל אותה.

    הכתובות מסוננות כאן ולא רק ב-``validate``, כי לא כל מה שמגיע לכאן
    עבר שם: הרשימה ה*לא מאומתת* נבנית מהשורה הגולמית כפי שהמודל
    החזיר אותה, וזו בדיוק הרשימה שאין לסמוך עליה. זו נקודת המעבר
    היחידה אל הדפדפן, ולכן הסינון יושב בה.
    """
    return {
        "part_number": row.get("part_number"),
        "manufacturer": row.get("manufacturer") or "",
        "tier": row.get("tier") or source.tier,
        "oe_number": row.get("oe_number") or "",
        "image_url": parts_discovery.safe_url(row.get("image_url")),
        "diagram_url": parts_discovery.safe_url(row.get("diagram_url")),
        "price_eur": row.get("price_eur"),
        "source_url": parts_discovery.safe_url(row.get("source_url")),
        "source_name": source.name,
        "confidence": row.get("confidence") or "",
        "note": row.get("note") or "",
        "part_id": part_id,
        "verified": verified,
        "reason": reason,
    }


def run_step(job, runner=None):
    """מקור אחד: שליפה, אימות, כתיבה. מחזיר את העבודה."""
    if not job.is_running:
        return job
    stages = job.stage_list
    if job.cursor >= len(stages):
        return _finish(job)

    vehicle = json.loads(job.vehicle)
    source = catalog_sources.get(stages[job.cursor])
    data = job.result_data
    lines = []

    if source is None:
        job.cursor += 1
        db.session.commit()
        return job

    # יומן נקי לכל שלב: מה שנרשם כאן שייך למקור הזה בלבד, ולא נגרר
    # מהמקור שרץ בבקשה הקודמת.
    trace.start()
    resume = Continuation(url=job.resume_url or "", hop=job.resume_hop or 0)
    try:
        candidates = (runner or _run_source)(
            source, vehicle, job.part_type, data, resume=resume
        )
    except Exception as exc:  # רשת, מפתח, מכסה או תשובה פגומה
        job.error = f"{source.name}: {exc}"
        job.resume_url, job.resume_hop = "", 0
        job.diagnosis = _diagnosis(source, exc)
        # דווקא בכשל היומן הוא כל מה שיש: ההודעה אומרת *מה* קרה,
        # והיומן אומר *איפה* - איזו כתובת נפתחה ומה חזר ממנה.
        job.log = _append_log(job, trace.lines() + [f"{source.name}: {exc}"])
        # מקור שנפל אינו "לא נמצא". בלי הסימון הזה תקלת רשת רגעית
        # הייתה נשמרת במטמון והופכת לתשובה שלילית לחודשיים.
        data["failed"] = True
        job.results = json.dumps(data, ensure_ascii=False)
        job.cursor += 1
        job.updated_at = _now()
        db.session.commit()
        if job.cursor >= len(stages):
            return _finish(job)
        return job

    # המקור לא סיים - הוא נעצר על תקציב הזמן ויש לו לאן להמשיך.
    # ‏cursor נשאר במקומו: אנחנו עדיין באותו מקור, רק צעד אחד הלאה.
    if resume.url:
        job.resume_url, job.resume_hop = resume.url[:500], resume.hop
        job.log = _append_log(job, trace.lines())
        job.diagnosis = _diagnosis(source)
        job.error = None
        job.updated_at = _now()
        db.session.commit()
        return job
    job.resume_url, job.resume_hop = "", 0

    rows = [candidate.as_row() for candidate in candidates]
    accepted, rejected = parts_discovery.validate(
        rows, vehicle.get("make") or "", vehicle.get("model") or "", job.part_type
    )
    for row in accepted:
        row["engine_code"] = vehicle.get("engine_code") or None
        row["year"] = vehicle.get("year")

    # מצב קריאה בלבד נועד להגן על הקטלוג מעריכה, לא לכבות את זרימת
    # הזיהוי. לכן השליפה רצה והתוצאה מוצגת - רק הכתיבה לקטלוג נדחית.
    read_only = bool(current_app.config.get("READ_ONLY"))
    if accepted and not read_only:
        created, updated = parts_discovery.save(
            accepted, source_note=SOURCE_NOTE, source_mark=SOURCE_MARK
        )
        job.saved += created
        lines.append(f"{source.name}: נוספו {created}, עודכנו {updated}")
    elif accepted:
        lines.append(f"{source.name}: {len(accepted)} תוצאות - לא נשמרו (קריאה בלבד)")
    else:
        lines.append(f"{source.name}: לא נמצאו תוצאות מאומתות")

    by_number = {}
    if accepted:
        numbers = [row["part_number"] for row in accepted]
        by_number = {
            part.part_number: part.id
            for part in Part.query.filter(Part.part_number.in_(numbers)).all()
        }
    for row in accepted:
        data["results"].append(
            _result_row(row, source, part_id=by_number.get(row["part_number"]))
        )
    # מה שלא עבר אימות מוצג ומסומן, ולא נכנס לקטלוג. הבחירה הזו מכוונת:
    # מכונאי שרואה שלוש אפשרויות עם רמת ודאות שונה מחליט לבד, ומכונאי
    # שרואה רק "לא נמצא" מרים טלפון.
    known = {row["part_number"] for row in accepted}
    for number, reason in rejected:
        if not number or number == "?" or number in known:
            continue
        raw = next((r for r in rows if r.get("part_number") == number), {})
        data["unverified"].append(
            _result_row(raw, source, verified=False, reason=reason)
        )
    if rejected:
        # הסיבה, ולא רק המספר: "מק"ט שנמצא ונפסל באימות" ו"מק"ט שלא
        # נמצא בכלל" נראים זהים על המסך, והתיקון שלהם שונה לגמרי.
        lines.append(f"    {len(rejected)} לא אומתו:")
        for number, reason in rejected[:20]:
            lines.append(f"      · {number or '?'} - {reason}")

    job.results = json.dumps(data, ensure_ascii=False)
    job.log = _append_log(job, trace.lines() + lines)
    job.diagnosis = _diagnosis(source, saved=bool(accepted) and not read_only,
                               read_only=read_only, accepted=len(accepted))
    job.error = None
    job.cursor += 1
    job.updated_at = _now()
    db.session.commit()
    if job.cursor >= len(stages):
        return _finish(job)
    return job


def _diagnosis(source, error=None, saved=None, read_only=False, accepted=0):
    """השלבים שנרשמו, בתוספת מה שרק ``run_step`` יודע.

    שני שלבים אינם ידועים למקור עצמו: אם בסוף נכתב משהו לקטלוג, ואם
    ‏READ_ONLY חסם את הכתיבה. שניהם מפרידים בין "לא נמצא" ל"נמצא ולא
    נשמר", וזו הבחנה שמכונאי מרגיש ומנהל צריך לראות.
    """
    rows = trace.stages()
    rows.insert(0, {"name": "המקור", "ok": error is None,
                    "detail": source.name if error is None
                    else f"{source.name}: {error}",
                    "hint": ""})
    if saved is not None:
        if read_only and accepted:
            rows.append({"name": "שמירה בקטלוג", "ok": False,
                         "detail": f"{accepted} מק\"טים לא נשמרו - מצב קריאה בלבד",
                         "hint": "כבה READ_ONLY כדי שהתוצאות ייכנסו לקטלוג."})
        elif saved:
            rows.append({"name": "שמירה בקטלוג", "ok": True,
                         "detail": f"{accepted} מק\"טים נשמרו", "hint": ""})
    return json.dumps(rows, ensure_ascii=False)[:8000]


def _append_log(job, lines):
    """מוסיף שורות ליומן העבודה, בתוך התקרה. מחזיר את היומן החדש."""
    text = "\n".join(str(line) for line in lines if str(line).strip())
    if not text:
        return job.log or ""
    return ((job.log or "") + text + "\n")[-LOG_CHARS:]


def _run_source(source, vehicle, part_type, data, resume=None):
    """הקריאה למקור עצמו. מופרדת כדי שבדיקות יזריקו במקומה."""
    oem_numbers = ()
    if source.tier != "oem":
        oem_numbers = known_oem_numbers(vehicle, part_type, data.get("results") or [])
    extra = {}
    # מקור שמביא עמוד אחד אינו צריך תקציב ואינו יודע לקבל אותו.
    # הדגל מפורש ולא introspection, כדי שכותב מקור חדש יבחר במודע.
    if getattr(source, "supports_resume", False):
        extra = {"resume": resume, "deadline": time.monotonic() + STEP_BUDGET}
    return source.lookup(vehicle, part_type, oem_numbers=oem_numbers, **extra)


def _finish(job):
    """סוגר את העבודה, ושומר את התשובה במטמון רק אם היא באמת תשובה.

    "לא נמצא" ראוי לשמירה - הוא חוסך את החיפוש הבא. "האתר לא ענה" לא:
    שמירה שלו הופכת תקלה של רגע לתשובה שלילית לחודשיים.

    נקרא רק כשכל המקורות רצו. עבודה שנעצרה באמצע (``stop_job``) אינה
    מגיעה לכאן, ולכן תשובה חלקית לא נכנסת למטמון - ראה שם.
    """
    job.status = LookupJob.DONE
    job.finished_at = _now()
    job.updated_at = _now()
    db.session.commit()
    data = job.result_data
    if not data.get("failed"):
        remember(
            json.loads(job.vehicle), job.part_type, data,
            sources=",".join(job.stage_list),
        )
    return job
