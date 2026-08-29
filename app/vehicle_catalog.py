"""קטלוג דגמי הרכב בישראל, ממאגר משרד התחבורה.

מאגר "תוצרים ודגמים של כלי רכב פרטי ומסחרי" ב-data.gov.il הוא נתון פתוח
(license: other-open) שמתעדכן יומית ומכיל למעלה מ-100 אלף רשומות.

למה זה חשוב כאן: התאמת מק"ט לרכב היא מה שמפעיל את החיפוש לפי מספר
רישוי. כשהיצרן והדגם מוקלדים כטקסט חופשי, "טויוטה" ו-"טויוטה יפן"
הם שני ערכים שונים וההצטלבות נכשלת. הקטלוג הזה נותן רשימה סגורה
ואמיתית לבחור ממנה.
"""
from sqlalchemy import UniqueConstraint

from .models import db


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


def models_for(make):
    query = db.session.query(VehicleModel.model).distinct()
    if make:
        query = query.filter(VehicleModel.make == make)
    return [row[0] for row in query.order_by(VehicleModel.model) if row[0]]
