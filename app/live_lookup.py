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
from datetime import datetime, timedelta, timezone

from flask import current_app
from sqlalchemy.exc import IntegrityError

from . import catalog_sources, parts_discovery, services
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
    STATUS_LABELS = {RUNNING: "מחפש", DONE: "הושלם",
                     FAILED: "נכשל", CANCELLED: "בוטל"}

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
    error = db.Column(db.Text)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), index=True)
    started_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    started_at = db.Column(db.DateTime, default=_now)
    updated_at = db.Column(db.DateTime, default=_now)
    finished_at = db.Column(db.DateTime)

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

    @property
    def stage_label(self):
        stages = self.stage_list
        if self.is_running and self.cursor < len(stages):
            source = catalog_sources.get(stages[self.cursor])
            return source.name if source else stages[self.cursor]
        return ""

    def to_dict(self):
        data = self.result_data
        return {
            "id": self.id,
            "status": self.status,
            "status_label": self.status_label,
            "stage_label": self.stage_label,
            "cursor": self.cursor,
            "total": self.total,
            "progress_pct": self.progress_pct,
            "is_running": self.is_running,
            "saved": self.saved,
            "error": self.error,
            "part_type": self.part_type,
            "part_type_name": type_name(self.part_type),
            "results": data.get("results") or [],
            "unverified": data.get("unverified") or [],
            "log": (self.log or "").strip().split("\n")[-20:] if self.log else [],
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
    db.session.add(job)
    db.session.commit()
    return job


def cancel_job(job):
    if job is not None and job.is_running:
        job.status = LookupJob.CANCELLED
        job.finished_at = _now()
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
    return {
        "part_number": row.get("part_number"),
        "manufacturer": row.get("manufacturer") or "",
        "tier": row.get("tier") or source.tier,
        "oe_number": row.get("oe_number") or "",
        "image_url": row.get("image_url") or "",
        "price_eur": row.get("price_eur"),
        "source_url": row.get("source_url") or "",
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

    try:
        candidates = (runner or _run_source)(source, vehicle, job.part_type, data)
    except Exception as exc:  # רשת, מפתח, מכסה או תשובה פגומה
        job.error = f"{source.name}: {exc}"
        job.log = ((job.log or "") + f"{source.name}: {exc}\n")[-4000:]
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
        lines.append(f"    {len(rejected)} לא אומתו")

    job.results = json.dumps(data, ensure_ascii=False)
    job.log = ((job.log or "") + "\n".join(lines) + "\n")[-4000:]
    job.error = None
    job.cursor += 1
    job.updated_at = _now()
    db.session.commit()
    if job.cursor >= len(stages):
        return _finish(job)
    return job


def _run_source(source, vehicle, part_type, data):
    """הקריאה למקור עצמו. מופרדת כדי שבדיקות יזריקו במקומה."""
    oem_numbers = ()
    if source.tier != "oem":
        oem_numbers = known_oem_numbers(vehicle, part_type, data.get("results") or [])
    return source.lookup(vehicle, part_type, oem_numbers=oem_numbers)


def _finish(job):
    """סוגר את העבודה, ושומר את התשובה במטמון רק אם היא באמת תשובה.

    "לא נמצא" ראוי לשמירה - הוא חוסך את החיפוש הבא. "האתר לא ענה" לא:
    שמירה שלו הופכת תקלה של רגע לתשובה שלילית לחודשיים.
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
