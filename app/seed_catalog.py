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

**קבוע ולא נגזר.** הרכבים נכתבים ל-``seed_vehicles`` ומשם נקראים.
רשימה שנגזרת מחדש בכל לחיצה זזה מתחת לרגליים - צילום צי חדש מחליף
את הסדר, וייבוא מרשם חדש מחליף את השלדה המייצגת - וזריעה שבנתה מאגר
במשך שבוע הייתה ממשיכה לשלדות אחרות ומפזרת את מה שנצבר.

**קצב.** מטרה אחת לכל פעולה, ולא הרצה של שעות. כל מטרה היא כמה בקשות
רשת וקריאות מודל שעולות כסף, והאדם שלוחץ צריך לראות מה קיבל לפני
שהוא משלם על הבא. העבודה נעצרת אחרי כל מק"ט ומחכה.
"""
import json
import sys
from datetime import datetime, timezone

from . import live_lookup, services, vehicles
from .catalog_sources import trace
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


class SeedVehicle(db.Model):
    """הצי הקבוע של הזריעה: הרכבים, כתובים ולא מחושבים מחדש בכל פעם.

    בלי הטבלה הזו הרשימה נגזרת מחדש בכל לחיצה: ``FleetModelCount``
    ממוין מחדש, ומהמרשם נשלף *רכב מייצג אחד* לכל דגם. שני הצדדים
    זזים - צילום צי חדש מחליף את הסדר, וייבוא מרשם חדש מחליף את
    השלדה - וכך אותה לחיצה מייצרת מחר רשימה אחרת.

    זו בעיה אמיתית ולא תיאורטית, כי הזריעה בונה מאגר לאורך זמן: מק"ט
    שהובא אתמול לשלדה א' לא נמצא מחר אם המערכת שואלת על שלדה ב', והצי
    שהושקע בו מתפזר. רשימה קבועה היא מה שהופך זריעה של שבוע לקטלוג
    ולא לאוסף ניסיונות.

    השורות נכתבות פעם אחת - מהצעה אוטומטית, אחרי עריכה - ומשם הן
    המקור. ``vehicles`` ו-``prime`` נשמרים כראיה למה הרכב נבחר, לא
    כמדד חי: הם הצילום שלפיו הוחלט.
    """

    __tablename__ = "seed_vehicles"

    id = db.Column(db.Integer, primary_key=True)
    # הסדר שבו נזרעים. הראשון הוא הנפוץ ביותר, וזה גם סדר התשלום.
    position = db.Column(db.Integer, default=0, nullable=False, index=True)
    vin = db.Column(db.String(32), nullable=False, unique=True)
    plate = db.Column(db.String(20), default="")
    make = db.Column(db.String(80), nullable=False)
    model = db.Column(db.String(120), nullable=False)
    year = db.Column(db.Integer)
    engine_code = db.Column(db.String(40), default="")
    model_code = db.Column(db.String(60), default="")
    # הצילום שלפיו נבחר, ולא ספירה חיה.
    vehicles = db.Column(db.Integer, default=0, nullable=False)
    prime = db.Column(db.Integer, default=0, nullable=False)
    # כיבוי רכב בלי למחוק אותו: הרשימה היא החלטה, וביטול החלטה
    # שמוחק את הראיה הוא ביטול שאי אפשר לחזור ממנו.
    active = db.Column(db.Boolean, default=True, nullable=False)
    note = db.Column(db.String(200), default="")
    created_at = db.Column(db.DateTime, default=_now)
    updated_at = db.Column(db.DateTime, default=_now)

    def as_row(self):
        return {
            "id": self.id,
            "position": self.position,
            "plate": self.plate or "",
            "vin": self.vin,
            "make": self.make,
            "model": self.model,
            "year": self.year,
            "engine_code": self.engine_code or "",
            "model_code": self.model_code or "",
            "vehicles": self.vehicles,
            "prime": self.prime,
            "active": self.active,
            "note": self.note or "",
        }


def fleet(include_inactive=False):
    """הצי הקבוע, לפי סדר. רשימה ריקה = עוד לא נקבע."""
    query = SeedVehicle.query
    if not include_inactive:
        query = query.filter_by(active=True)
    return query.order_by(SeedVehicle.position, SeedVehicle.id).all()


def fleet_is_set():
    return db.session.query(SeedVehicle.id).first() is not None


def save_fleet(rows):
    """כותב את הצי, ומחליף את מה שהיה. מחזיר את השורות שנשמרו.

    החלפה ולא מיזוג: הרשימה היא החלטה אחת ולא אוסף שנצבר, ומיזוג היה
    מותיר בה רכב שנמחק בעריכה. כפילות שלדה נבלעת - אותו רכב פעמיים
    הוא אותה זריעה פעמיים, על חשבון מי שלוחץ.
    """
    seen, clean = set(), []
    for row in rows or ():
        vin = str((row or {}).get("vin") or "").strip().upper()
        if not vin or vin in seen:
            continue
        seen.add(vin)
        clean.append((vin, row))
    if not clean:
        raise ValueError("אין רכבים לשמור.")

    SeedVehicle.query.delete()
    saved = []
    for position, (vin, row) in enumerate(clean):
        vehicle = SeedVehicle(
            position=position,
            vin=vin,
            plate=str(row.get("plate") or "")[:20],
            make=str(row.get("make") or "")[:80],
            model=str(row.get("model") or "")[:120],
            year=_as_int(row.get("year")),
            engine_code=str(row.get("engine_code") or "")[:40],
            model_code=str(row.get("model_code") or "")[:60],
            vehicles=_as_int(row.get("vehicles")) or 0,
            prime=_as_int(row.get("prime")) or 0,
            active=bool(row.get("active", True)),
            note=str(row.get("note") or "")[:200],
        )
        db.session.add(vehicle)
        saved.append(vehicle)
    db.session.commit()
    return saved


def clear_fleet():
    """מוחק את הצי הקבוע. הלחיצה הבאה תציע רשימה חדשה מהנתונים."""
    removed = SeedVehicle.query.delete()
    db.session.commit()
    return removed


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _fleet_rows(limit, lookup=None):
    """הרכבים שייזרעו, ומאיפה הם באו.

    מחזיר (רכבים, דילוגים, קבוע). ``קבוע`` אומר שהרשימה נקראה
    מהטבלה ולא נגזרה מחדש - וזו ההבחנה שקובעת אם אותה לחיצה מחר
    תיתן את אותו דבר.
    """
    saved = fleet()
    if saved:
        return [{
            "plate": row.plate or "",
            "vin": row.vin,
            "make": row.make,
            "model": row.model,
            "year": row.year,
            "engine_code": row.engine_code or "",
            "model_code": row.model_code or "",
            "vehicles": row.vehicles,
            "prime": row.prime,
        } for row in saved[:limit]], [], True

    rows, skipped = [], []
    for row in ranked_models(limit):
        vehicle = a_vehicle_of(row.search_make or row.make, row.model, lookup=lookup)
        if vehicle is None:
            skipped.append(f"{row.make} {row.model}")
            continue
        rows.append({
            "plate": vehicle.get("plate") or "",
            "vin": vehicle.get("vin") or "",
            "make": vehicle.get("make") or row.make,
            "model": vehicle.get("model") or row.model,
            "year": vehicle.get("year"),
            "engine_code": vehicle.get("engine_code") or "",
            "model_code": vehicle.get("model_code") or "",
            "vehicles": row.vehicles,
            "prime": row.prime,
        })
    return rows, skipped, False


def propose(limit=DEFAULT_VEHICLES, part_types=None, lookup=None):
    """הצעת רשימת מטרות: רכבים × חלקים נצרכים.

    מחזיר (מטרות, דילוגים). דילוג הוא דגם שאין לו רכב עם שלדה במרשם,
    והוא מוצג ולא נבלע - "הצענו 7 מתוך 10" היא עובדה שצריך לראות.
    כשהצי כבר נקבע אין דילוגים בכלל: הרשימה נקראת ולא נבנית.
    """
    return propose_detailed(limit, part_types, lookup)[:2]


def propose_detailed(limit=DEFAULT_VEHICLES, part_types=None, lookup=None):
    """כמו ``propose``, ובנוסף אם הרשימה קבועה. מחזיר (מטרות, דילוגים, קבוע)."""
    types = [t for t in (part_types or DEFAULT_PART_TYPES) if t in PART_TYPES]
    rows, skipped, fixed = _fleet_rows(limit, lookup=lookup)
    targets = []
    for vehicle in rows:
        for part_type in types:
            targets.append({
                **vehicle,
                "part_type": part_type,
                "part_type_name": type_name(part_type),
            })
    return targets, skipped, fixed


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
    """פותח זריעה. זריעה פעילה קיימת מוחזרת כמות שהיא.

    זריעה ראשונה גם *קובעת* את הצי: הרכבים שנבחרו בפועל נכתבים
    לטבלה. בלי זה הרשימה נשארת נגזרת, והזריעה הבאה - שבועיים ומאתיים
    מק"טים אחר כך - הייתה יוצאת לשלדות אחרות ומפזרת את מה שנצבר.
    מי שרוצה רשימה אחרת מוחק אותה במסך; מי ששותק מקבל יציבות.
    """
    existing = active_job()
    if existing is not None:
        return existing
    if not targets:
        raise ValueError("אין מטרות לזרוע.")
    if not fleet_is_set():
        save_fleet(targets)
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
    """שורה ליומן הזריעה, ובמקביל ליומן הריצה של השירות.

    הזריעה היא התהליך שרץ בלי שאיש מסתכל על המסך: לוחצים, וחוזרים
    כעבור רבע שעה לראות כמה נמצא. השורה ב-stdout היא מה שמאפשר
    לראות *איזו* מטרה נפלה ולמה, אחרי שהחלון כבר נסגר.
    """
    job.log = ((job.log or "") + line + "\n")[-20000:]
    if trace.TO_STDOUT:
        try:
            sys.stdout.write(f"{trace.MARKER} זריעה {job.id}: {line}\n")
            sys.stdout.flush()
        except Exception:
            pass


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
