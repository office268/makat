"""זריעת הקטלוג: להביא מק"טים מראש, במקום לחכות שמכונאי יבקש.

השליפה החיה עונה על שאלה אחת בכל פעם, ובפעם הראשונה לכל דגם היא
לוקחת דקות. זה בסדר כשהמכונאי שאל משהו נדיר; זה לא בסדר כשהוא שאל
רפידות לקורולה - החלק הכי שגרתי שיש, על אחד הרכבים הנפוצים בישראל.

לכן המסך הזה: בוחרים מראש רכבים נפוצים וחלקים שנצרכים, ומביאים את
המק"טים לקטלוג *לפני* שמישהו שואל. משם והלאה אותה שאלה נענית מיידית
מהמאגר המקומי, בלי רשת ובלי מודל.

**מי נבחר, ולמה זו לא דעה.** ``FleetModelCount`` כבר סופר כמה רכבים
מכל דגם באמת נוסעים היום בישראל, ומפלח אותם לפי גיל. העמודה ``prime``
היא בני 4-12 - חלון האפטרמרקט: לא חדשים (עדיין אצל היבואן) ולא ישנים
מכדי שיושקע בהם. זה בדיוק "נפוץ מאוד ויש לו פוטנציאל מכירה", והוא
נמדד ולא משוער.

**קצב.** מטרה אחת לכל פעולה, ולא הרצה של שעות. כל מטרה היא כמה בקשות
רשת וקריאות מודל שעולות כסף, והאדם שלוחץ צריך לראות מה קיבל לפני
שהוא משלם על הבא. העבודה נעצרת אחרי כל מק"ט ומחכה.
"""
import json
from datetime import datetime, timezone

from . import live_lookup, services, vehicles
from .fleet_stats import FleetModelCount
from .models import db
from .taxonomy import PART_TYPES, type_name


def _now():
    return datetime.now(timezone.utc)


# עשרת החלקים. כולם פריטים שקטלוג יצרן באמת מחזיק, וכולם מתחלפים
# בשגרה - זה מה שמצדיק להביא אותם מראש. חלק שמתחלף פעם בחיי הרכב
# יבוזבז עליו אותו זמן שליפה בדיוק, ויישב במאגר בלי שיישאל.
DEFAULT_PART_TYPES = [
    "brake_pads_front",
    "brake_pads_rear",
    "brake_disc_front",
    "oil_filter",
    "air_filter",
    "cabin_filter",
    "fuel_filter",
    "spark_plug",
    "serpentine_belt",
    "shock_absorber_front",
]

DEFAULT_VEHICLES = 10


# --------------------------------------------------------------------------
# מי נזרע
# --------------------------------------------------------------------------

def ranked_models(limit=DEFAULT_VEHICLES):
    """הדגמים המובילים לפי חלון האפטרמרקט, ואם אין - לפי סך הרכבים.

    ``prime`` הוא בני 4-12 ולכן הוא המדד הנכון, אבל הוא נספר רק
    כשהצילום כלל פילוח גיל. נפילה לאחור ל-``vehicles`` עדיפה על מסך
    ריק שאומר "אין נתונים" כשיש.
    """
    query = FleetModelCount.query
    if db.session.query(FleetModelCount.id).filter(
        FleetModelCount.prime > 0
    ).first():
        query = query.order_by(FleetModelCount.prime.desc())
    else:
        query = query.order_by(FleetModelCount.vehicles.desc())
    return query.limit(limit).all()


def a_vehicle_of(make, model, lookup=None):
    """רכב אמיתי אחד מהדגם הזה, מהמרשם - כדי שתהיה לנו שלדה.

    השליפה מקטלוג היצרן היא *לפי שלדה*, ולכן דגם בלי רכב מייצג אינו
    מטרה שאפשר לזרוע. המרשם הוא המקום היחיד שבו יש שלדות אמיתיות,
    והוא כבר בשימוש לזיהוי לפי מספר רישוי.
    """
    finder = lookup or vehicles.by_model
    found = finder(make, model)
    if not found or not (found.get("vin") or "").strip():
        return None
    return found


def propose(limit=DEFAULT_VEHICLES, part_types=None, lookup=None):
    """הצעת רשימת מטרות: רכבים מובילים × חלקים נצרכים.

    מחזיר (מטרות, דילוגים). דילוג הוא דגם שאין לו רכב עם שלדה במרשם,
    והוא מוצג ולא נבלע - "הצענו 7 מתוך 10" היא עובדה שצריך לראות.
    """
    types = [t for t in (part_types or DEFAULT_PART_TYPES) if t in PART_TYPES]
    targets, skipped = [], []
    for row in ranked_models(limit):
        vehicle = a_vehicle_of(row.search_make or row.make, row.model, lookup=lookup)
        if vehicle is None:
            skipped.append(f"{row.make} {row.model}")
            continue
        for part_type in types:
            targets.append({
                "plate": vehicle.get("plate") or "",
                "vin": vehicle.get("vin") or "",
                "make": vehicle.get("make") or row.make,
                "model": vehicle.get("model") or row.model,
                "year": vehicle.get("year"),
                "engine_code": vehicle.get("engine_code") or "",
                "model_code": vehicle.get("model_code") or "",
                "part_type": part_type,
                "part_type_name": type_name(part_type),
                "vehicles": row.vehicles,
                "prime": row.prime,
            })
    return targets, skipped


# --------------------------------------------------------------------------
# העבודה
# --------------------------------------------------------------------------

class SeedJob(db.Model):
    """זריעה אחת. מטרה = (רכב, סוג חלק), ומטרה אחת בכל פעם.

    שני מונים ולא אחד: ``cursor`` הוא כמה מטרות הושלמו, ו-``child_id``
    הוא השליפה החיה שרצה *עכשיו*. השליפה עצמה נמשכת כמה בקשות (היא
    מנווטת בקטלוג), ולכן בקשת צעד אחת אינה בהכרח מטרה אחת - היא
    מקדמת את השליפה הפנימית, והמטרה נסגרת רק כשזו הסתיימה.
    """

    __tablename__ = "seed_jobs"

    RUNNING, DONE, CANCELLED = "running", "done", "cancelled"
    STATUS_LABELS = {RUNNING: "בתהליך", DONE: "הושלם", CANCELLED: "בוטל"}

    id = db.Column(db.Integer, primary_key=True)
    status = db.Column(db.String(20), default=RUNNING, nullable=False, index=True)
    targets = db.Column(db.Text, nullable=False)      # JSON
    cursor = db.Column(db.Integer, default=0, nullable=False)
    # השליפה החיה שרצה כרגע. ריק = בין מטרות, ואז לחיצה פותחת את הבאה.
    child_id = db.Column(db.Integer)
    found = db.Column(db.Integer, default=0, nullable=False)
    missing = db.Column(db.Integer, default=0, nullable=False)
    failed = db.Column(db.Integer, default=0, nullable=False)
    saved = db.Column(db.Integer, default=0, nullable=False)
    # מה קרה במטרה האחרונה שנסגרה - זה מה שהאדם רואה לפני שהוא מחליט
    # אם לשלם על הבאה.
    last_result = db.Column(db.Text, default="")      # JSON
    log = db.Column(db.Text, default="")
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
        return min(100, round(self.cursor * 100 / self.total)) if self.total else 100

    @property
    def current(self):
        """המטרה שרצה עכשיו, או הבאה בתור."""
        targets = self.target_list
        return targets[self.cursor] if self.cursor < len(targets) else None

    @property
    def awaiting(self):
        """מטרה נסגרה ויש עוד - כאן עוצרים ומחכים ללחיצה.

        זה הלב של הקצב שנבחר: כל מטרה עולה כסף, והאדם שלוחץ רואה מה
        קיבל לפני שהוא משלם על הבאה.
        """
        return self.is_running and not self.child_id and 0 < self.cursor < self.total

    def describe(self, target):
        if not target:
            return ""
        return (f"{target.get('make')} {target.get('model')} "
                f"{target.get('year') or ''} · {type_name(target.get('part_type'))}")

    def to_dict(self):
        try:
            last = json.loads(self.last_result) if self.last_result else None
        except ValueError:
            last = None
        return {
            "id": self.id,
            "status": self.status,
            "status_label": self.STATUS_LABELS.get(self.status, self.status),
            "cursor": self.cursor,
            "total": self.total,
            "progress_pct": self.progress_pct,
            "is_running": self.is_running,
            "awaiting": self.awaiting,
            "running_now": bool(self.child_id),
            "current": self.describe(self.current),
            "found": self.found, "missing": self.missing,
            "failed": self.failed, "saved": self.saved,
            "last_result": last,
            "error": self.error,
            "log": (self.log or "").strip().split("\n")[-40:] if self.log else [],
        }


def active_job():
    return (SeedJob.query.filter_by(status=SeedJob.RUNNING)
            .order_by(SeedJob.id.desc()).first())


def latest_job():
    return SeedJob.query.order_by(SeedJob.id.desc()).first()


def start_job(targets, user_id=None):
    """פותח זריעה. זריעה פעילה קיימת מוחזרת כמות שהיא."""
    existing = active_job()
    if existing is not None:
        return existing
    if not targets:
        raise ValueError("אין מטרות לזרוע.")
    job = SeedJob(targets=json.dumps(targets, ensure_ascii=False),
                  started_by_id=user_id, log="")
    db.session.add(job)
    db.session.commit()
    return job


def cancel_job(job):
    if job is not None and job.is_running:
        if job.child_id:
            live_lookup.cancel_job(db.session.get(live_lookup.LookupJob, job.child_id))
            job.child_id = None
        job.status = SeedJob.CANCELLED
        job.finished_at = _now()
        db.session.commit()
    return job


# --------------------------------------------------------------------------
# הצעד
# --------------------------------------------------------------------------

def _vehicle_of(target):
    """הרכב כפי ש-``live_lookup`` מצפה לקבל אותו."""
    return {
        "plate": target.get("plate") or "",
        "vin": target.get("vin") or "",
        "make": target.get("make") or "",
        "model": target.get("model") or "",
        "year": target.get("year"),
        "engine_code": target.get("engine_code") or "",
        "model_code": target.get("model_code") or "",
        "source": "seed",
    }


def _note(job, line):
    job.log = ((job.log or "") + line + "\n")[-20000:]


def _close(job, target, outcome, detail, numbers=(), saved=0):
    """סוגר מטרה: סופר, רושם, ומקדם. השליפה הפנימית נמחקת."""
    counters = {"found": "found", "missing": "missing", "failed": "failed"}
    setattr(job, counters[outcome], getattr(job, counters[outcome]) + 1)
    job.saved += saved
    job.last_result = json.dumps({
        "target": job.describe(target),
        "part_type_name": type_name(target.get("part_type")),
        "outcome": outcome,
        "detail": detail,
        "numbers": list(numbers),
    }, ensure_ascii=False)
    mark = {"found": "✓", "missing": "—", "failed": "✗"}[outcome]
    _note(job, f"{mark} {job.describe(target)}: {detail}")
    job.child_id = None
    job.cursor += 1
    job.updated_at = _now()
    if job.cursor >= job.total:
        job.status = SeedJob.DONE
        job.finished_at = _now()
    db.session.commit()
    return job


def run_step(job, user=None):
    """מקדם את הזריעה בצעד אחד. מחזיר את העבודה.

    שלושה מצבים, ורק אחד מהם פותח בקשת רשת חדשה:

    1. אין שליפה פנימית - פותחים אחת למטרה הנוכחית. אם התשובה כבר
       בקטלוג או במטמון, סוגרים מיד בלי לצאת לרשת בכלל.
    2. יש שליפה פנימית - מקדמים אותה בצעד אחד (היא מנווטת בקטלוג,
       וזה נמשך כמה בקשות).
    3. השליפה הסתיימה - סופרים, רושמים, ועוצרים עד ללחיצה הבאה.
    """
    if not job.is_running:
        return job
    target = job.current
    if target is None:
        job.status = SeedJob.DONE
        job.finished_at = _now()
        db.session.commit()
        return job

    vehicle = _vehicle_of(target)
    part_type = target.get("part_type")

    if job.child_id:
        child = db.session.get(live_lookup.LookupJob, job.child_id)
        if child is None:
            job.child_id = None
            db.session.commit()
            return job
        if child.is_running:
            # אישור המשך אינו נשאל כאן: הזריעה *רוצה* את כל המקורות,
            # ומי שלחץ עליה כבר החליט. השער נועד למכונאי מול מסך.
            live_lookup.run_step(child)
            job.updated_at = _now()
            db.session.commit()
            return job
        return _finish_child(job, target, child)

    # הקטלוג המקומי קודם. זריעה שמביאה מה שכבר יש היא בקשת רשת
    # מיותרת וקריאת מודל מיותרת, על חשבון הקרדיטים של מי שלחץ.
    local = services.parts_for_vehicle(vehicle, part_type)
    if local:
        numbers = [part.part_number for part in local]
        return _close(job, target, "found",
                      f"כבר בקטלוג ({len(numbers)})", numbers)
    hit = live_lookup.cached(vehicle, part_type)
    if hit is not None:
        numbers = [row.get("part_number") for row in (hit.get("results") or [])]
        if numbers:
            return _close(job, target, "found",
                          f"מתשובה שמורה ({len(numbers)})", numbers)
        return _close(job, target, "missing", "תשובה שמורה: אין מק\"ט לחלק הזה")

    try:
        child = live_lookup.start_job(vehicle, part_type, user=user)
    except ValueError as exc:
        return _close(job, target, "failed", str(exc))
    job.child_id = child.id
    job.updated_at = _now()
    _note(job, f"→ {job.describe(target)}")
    db.session.commit()
    return job


def _finish_child(job, target, child):
    """השליפה הפנימית נגמרה - מתרגמים אותה לתוצאת מטרה."""
    data = child.result_data
    numbers = [row.get("part_number") for row in (data.get("results") or [])]
    if numbers:
        return _close(job, target, "found", f"נמצאו {len(numbers)}",
                      numbers, saved=child.saved or 0)
    if child.status == live_lookup.LookupJob.FAILED or data.get("failed"):
        return _close(job, target, "failed", child.error or "השליפה נכשלה")
    unverified = len(data.get("unverified") or [])
    if unverified:
        return _close(job, target, "missing",
                      f"{unverified} מועמדים שלא אומתו - לא נשמרו")
    return _close(job, target, "missing", 'לא נמצא מק"ט לחלק הזה ברכב הזה')
