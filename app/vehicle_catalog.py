"""קטלוג דגמי הרכב בישראל, ממאגר משרד התחבורה.

מאגר "תוצרים ודגמים של כלי רכב פרטי ומסחרי" ב-data.gov.il הוא נתון פתוח
(license: other-open) שמתעדכן יומית ומכיל למעלה מ-100 אלף רשומות.

למה זה חשוב כאן: התאמת מק"ט לרכב היא מה שמפעיל את החיפוש לפי מספר
רישוי. כשהיצרן והדגם מוקלדים כטקסט חופשי, "טויוטה" ו-"טויוטה יפן"
הם שני ערכים שונים וההצטלבות נכשלת. הקטלוג הזה נותן רשימה סגורה
ואמיתית לבחור ממנה.
"""
from datetime import datetime, timezone

from sqlalchemy import UniqueConstraint

from .models import db


def _now():
    return datetime.now(timezone.utc)


class VehicleModel(db.Model):
    """דגם רכב אחד, מכווץ לטווח שנים.

    המאגר המקורי מחזיק שורה לכל שנת ייצור. כאן מאחדים אותן לשורה אחת
    עם year_from ו-year_to, כי זה הפורמט שהתאמת חלף עובדת בו.
    """

    __tablename__ = "vehicle_models"

    id = db.Column(db.Integer, primary_key=True)
    make = db.Column(db.String(80), nullable=False, index=True)      # tozar
    model = db.Column(db.String(120), nullable=False, index=True)    # kinuy_mishari
    model_code = db.Column(db.String(60), index=True)                # degem_nm
    trim = db.Column(db.String(80))                                  # ramat_gimur
    year_from = db.Column(db.Integer, index=True)
    year_to = db.Column(db.Integer, index=True)
    engine_volume = db.Column(db.Integer)                            # nefah_manoa, סמ"ק
    fuel = db.Column(db.String(40))                                  # delek_nm
    body = db.Column(db.String(60))                                  # merkav
    horsepower = db.Column(db.Integer)                               # koah_sus

    __table_args__ = (
        UniqueConstraint("make", "model", "model_code", "trim", name="uq_vehicle_model"),
    )

    @property
    def years(self):
        if self.year_from and self.year_to and self.year_from != self.year_to:
            return f"{self.year_from}-{self.year_to}"
        return str(self.year_from or self.year_to or "")

    @property
    def label(self):
        """תווית לבחירה ברשימה.

        כוללת קוד דגם ונפח מנוע, כי לאותו דגם מסחרי יש כמה קודי דגם
        רשמיים שנבדלים במנוע ובהספק - בלעדיהם הרשימה נראית משוכפלת.
        """
        parts = [self.make, self.model]
        if self.years:
            parts.append(self.years)
        if self.engine_volume:
            parts.append(f'{self.engine_volume} סמ"ק')
        if self.horsepower:
            parts.append(f'{self.horsepower} כ"ס')
        if self.model_code:
            parts.append(self.model_code)
        return " · ".join(p for p in parts if p)

    def to_dict(self):
        return {
            "id": self.id,
            "make": self.make,
            "model": self.model,
            "model_code": self.model_code,
            "trim": self.trim,
            "year_from": self.year_from,
            "year_to": self.year_to,
            "years": self.years,
            "engine_volume": self.engine_volume,
            "fuel": self.fuel,
            "body": self.body,
            "label": self.label,
        }

    def __repr__(self):
        return f"<VehicleModel {self.make} {self.model} {self.years}>"


class VehicleImportJob(db.Model):
    """הרצת ייבוא אחת של הקטלוג, עם נקודת ההמשך שלה.

    הייבוא רץ במנות מהדפדפן: כל בקשה מייבאת כמה עמודים ומקדמת את offset.
    ההתקדמות יושבת ב-DB ולא בזיכרון, כי gunicorn מריץ כמה workers ובקשה
    אחת לא בהכרח נוחתת אצל מי שטיפל בקודמת - וגם כדי שנפילה באמצע לא
    תאבד את מה שכבר יובא.
    """

    __tablename__ = "vehicle_import_jobs"

    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"

    STATUS_LABELS = {
        RUNNING: "בתהליך",
        DONE: "הושלם",
        FAILED: "נכשל",
        CANCELLED: "בוטל",
    }

    id = db.Column(db.Integer, primary_key=True)
    status = db.Column(db.String(20), default=RUNNING, nullable=False, index=True)
    offset = db.Column(db.Integer, default=0, nullable=False)  # ה-offset הבא למשיכה
    total = db.Column(db.Integer)          # סה"כ רשומות לפי המאגר
    fetched = db.Column(db.Integer, default=0, nullable=False)   # רשומות גולמיות
    created = db.Column(db.Integer, default=0, nullable=False)   # דגמים שנוספו
    updated = db.Column(db.Integer, default=0, nullable=False)   # דגמים שעודכנו
    error = db.Column(db.Text)             # השגיאה האחרונה, גם אם ההרצה ממשיכה
    failures = db.Column(db.Integer, default=0, nullable=False)  # כשלונות רצופים
    started_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    started_at = db.Column(db.DateTime, default=_now)
    updated_at = db.Column(db.DateTime, default=_now)
    finished_at = db.Column(db.DateTime)

    started_by = db.relationship("User", foreign_keys=[started_by_id])

    @property
    def is_running(self):
        return self.status == self.RUNNING

    @property
    def progress_pct(self):
        if not self.total:
            return 0
        return min(100, round(self.offset * 100 / self.total))

    @property
    def status_label(self):
        return self.STATUS_LABELS.get(self.status, self.status)

    @property
    def action_label(self):
        """מה הכפתור עושה בפועל מהמצב הנוכחי.

        הרצה שנעצרה באמצע ממשיכה מנקודת העצירה; הרצה שהושלמה מתחילה
        מחדש ומושכת את כל המאגר. שני דברים שונים לגמרי, ולכן הכפתור
        חייב להגיד מי מהם - מקור אמת אחד לתבנית ול-JS גם יחד.
        """
        return "ייבוא מחדש" if self.status == self.DONE else "המשך ייבוא"

    def to_dict(self):
        return {
            "id": self.id,
            "status": self.status,
            "status_label": self.status_label,
            "action_label": self.action_label,
            "offset": self.offset,
            "total": self.total,
            "fetched": self.fetched,
            "created": self.created,
            "updated": self.updated,
            "error": self.error,
            "failures": self.failures,
            "progress_pct": self.progress_pct,
            "is_running": self.is_running,
            "models_in_catalog": VehicleModel.query.count(),
        }

    def __repr__(self):
        return f"<VehicleImportJob {self.id} {self.status} @{self.offset}>"


def active_job():
    """ההרצה הפתוחה, אם יש. יותר מאחת במקביל תילחם על אותן שורות."""
    return (
        VehicleImportJob.query.filter_by(status=VehicleImportJob.RUNNING)
        .order_by(VehicleImportJob.id.desc())
        .first()
    )


def latest_job():
    return VehicleImportJob.query.order_by(VehicleImportJob.id.desc()).first()


def _clean(value):
    return (str(value).strip() if value is not None else "") or None


def _int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def collapse_records(records):
    """ממיר רשומות גולמיות מהמאגר לדגמים עם טווחי שנים.

    המאגר מחזיק שורה לכל שנת ייצור של אותו דגם. מקבצים לפי
    (יצרן, דגם, קוד דגם, רמת גימור) ולוקחים את המינימום והמקסימום.
    """
    grouped = {}
    for record in records:
        make = _clean(record.get("tozar"))
        model = _clean(record.get("kinuy_mishari")) or _clean(record.get("degem_nm"))
        if not make or not model:
            continue

        key = (
            make,
            model,
            _clean(record.get("degem_nm")),
            _clean(record.get("ramat_gimur")),
        )
        year = _int(record.get("shnat_yitzur"))
        entry = grouped.get(key)
        if entry is None:
            grouped[key] = {
                "make": key[0], "model": key[1],
                "model_code": key[2], "trim": key[3],
                "year_from": year, "year_to": year,
                "engine_volume": _int(record.get("nefah_manoa")),
                "fuel": _clean(record.get("delek_nm")),
                "body": _clean(record.get("merkav")),
                "horsepower": _int(record.get("koah_sus")),
            }
        elif year:
            if entry["year_from"] is None or year < entry["year_from"]:
                entry["year_from"] = year
            if entry["year_to"] is None or year > entry["year_to"]:
                entry["year_to"] = year
    return list(grouped.values())


def upsert(rows):
    """שומר דגמים. מעדכן טווחי שנים של דגמים קיימים במקום לשכפל."""
    created = updated = 0
    for row in rows:
        existing = VehicleModel.query.filter_by(
            make=row["make"], model=row["model"],
            model_code=row["model_code"], trim=row["trim"],
        ).first()
        if existing is None:
            db.session.add(VehicleModel(**row))
            created += 1
            continue

        changed = False
        for field in ("year_from", "year_to"):
            new = row.get(field)
            current = getattr(existing, field)
            if new is None:
                continue
            if current is None or (field == "year_from" and new < current) \
                    or (field == "year_to" and new > current):
                setattr(existing, field, new)
                changed = True
        if changed:
            updated += 1
    db.session.commit()
    return created, updated


def makes():
    rows = db.session.query(VehicleModel.make).distinct().order_by(VehicleModel.make)
    return [row[0] for row in rows if row[0]]


def popular_models(make=None, limit=5):
    """דגמים לפי מספר הווריאנטים הרשמיים שלהם במאגר.

    אין במאגר נתוני מכירות, אבל לדגם נפוץ יש יותר קודי דגם, רמות גימור
    ומנועים רשומים. זה הפרוקסי היחיד לפופולריות שאפשר לגזור ממה שיש,
    והוא מספיק כדי לבחור מדגם סביר במקום לשלוף דגמים אקראיים.
    """
    query = db.session.query(
        VehicleModel.make, VehicleModel.model, db.func.count().label("variants")
    )
    if make:
        query = query.filter(VehicleModel.make == make)
    rows = (
        query.group_by(VehicleModel.make, VehicleModel.model)
        .order_by(db.desc("variants"), VehicleModel.make, VehicleModel.model)
        .limit(limit)
        .all()
    )
    return [(row[0], row[1]) for row in rows]


def popular_makes(limit=2):
    """יצרנים לפי מספר הדגמים שרשומים להם."""
    rows = (
        db.session.query(VehicleModel.make, db.func.count().label("models"))
        .group_by(VehicleModel.make)
        .order_by(db.desc("models"), VehicleModel.make)
        .limit(limit)
        .all()
    )
    return [row[0] for row in rows]


def models_for(make):
    query = db.session.query(VehicleModel.model).distinct()
    if make:
        query = query.filter(VehicleModel.make == make)
    return [row[0] for row in query.order_by(VehicleModel.model) if row[0]]
