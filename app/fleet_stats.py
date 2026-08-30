"""כמה רכבים מכל דגם באמת נוסעים היום בישראל.

מאגר "כלי רכב פרטיים ומסחריים" של משרד התחבורה ב-data.gov.il מחזיק שורה
לכל רכב פעיל במרשם - כשלושה מיליון שורות. זה אותו מאגר שהזיהוי לפי מספר
רישוי עובד מולו (app/vehicles.py), ולכן גם מזהה המשאב מגיע משם.

הספירה עצמה נעשית בצד המאגר: CKAN חושף נקודת SQL, ו-GROUP BY אחד מחזיר
את הפילוח לפי דגם בכמה בקשות במקום למשוך שלושה מיליון שורות. נקודת ה-SQL
לא תמיד פתוחה שם, ולכן יש מסלול שני - דפדוף רגיל עם צבירה מקומית
(scan_counts), ומסלול שלישי לצבירה מקובץ שהורד ידנית (aggregate_records).
שלושתם מייצרים את אותו מבנה שורה בדיוק.

למה זה כאן ולא רק כסקריפט: הקטלוג יודע אילו דגמים קיימים, לא כמה מהם על
הכביש. דגם עם 90 אלף רכבים ודגם עם 300 נראים אותו דבר ברשימת הדגמים, והם
לא אותו דבר כשמחליטים איזה מק"ט להחזיק במלאי.
"""
import csv
import io
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from .models import db
from .vehicles import RESOURCE_ID  # מקור אמת אחד למאגר הרכבים הפעילים

CKAN_SQL_URL = "https://data.gov.il/api/3/action/datastore_search_sql"
CKAN_URL = "https://data.gov.il/api/3/action/datastore_search"
TIMEOUT = float(os.environ.get("GOV_STATS_TIMEOUT", "60"))

# נקודת ה-SQL של CKAN חוסמת תשובות ענק, ולכן גם הצבירה נמשכת בעמודים
SQL_PAGE_SIZE = 10000
SCAN_PAGE_SIZE = 1000

# שמות השדות במאגר, בתעתיק של משרד התחבורה
FIELD_MAKE = "tozeret_nm"        # יצרן, למשל "טויוטה יפן"
FIELD_MODEL = "kinuy_mishari"    # כינוי מסחרי, למשל "COROLLA"
FIELD_CODE = "degem_nm"          # קוד דגם רשמי, מבדיל בין וריאנטים
FIELD_YEAR = "shnat_yitzur"

SCAN_FIELDS = (FIELD_MAKE, FIELD_MODEL, FIELD_CODE, FIELD_YEAR)

UNKNOWN = "לא ידוע"

COUNT_SQL = (
    'SELECT "{make}" AS make, "{model}" AS model, "{code}" AS model_code, '
    "count(*) AS vehicles, "
    'min("{year}"::text) AS year_from, max("{year}"::text) AS year_to '
    'FROM "{resource}" '
    "GROUP BY 1, 2, 3 HAVING count(*) >= {min_count} "
    "ORDER BY vehicles DESC, 1, 2, 3 LIMIT {limit} OFFSET {offset}"
)


def _now():
    return datetime.now(timezone.utc)


class FleetModelCount(db.Model):
    """דגם אחד וכמה רכבים ממנו פעילים במרשם.

    זהו צילום מצב: המאגר הממשלתי מתעדכן יומית, והטבלה הזאת מחזיקה את
    התמונה של רגע המשיכה בלבד. לכן כל טעינה מחליפה את הקודמת במלואה -
    חצי מהספירה הישנה וחצי מהחדשה זו טבלה שלא מסתכמת לשום מספר אמיתי.
    """

    __tablename__ = "fleet_model_counts"

    id = db.Column(db.Integer, primary_key=True)
    make = db.Column(db.String(80), nullable=False, index=True)
    model = db.Column(db.String(120), nullable=False, index=True)
    model_code = db.Column(db.String(60), index=True)
    vehicles = db.Column(db.Integer, nullable=False, index=True)
    year_from = db.Column(db.Integer)
    year_to = db.Column(db.Integer)
    taken_at = db.Column(db.DateTime, default=_now, nullable=False)

    @property
    def years(self):
        if self.year_from and self.year_to and self.year_from != self.year_to:
            return f"{self.year_from}-{self.year_to}"
        return str(self.year_from or self.year_to or "")

    @property
    def search_make(self):
        """היצרן כפי שחיפוש החלפים מחפש אותו.

        במרשם היצרן הוא "טויוטה יפן" - שם היצרן ומדינת הייצור. ההתאמות
        בקטלוג רשומות על "טויוטה" בלבד, וזו בדיוק הנורמליזציה ש-
        services.parts_for_vehicle עושה לרכב שזוהה לפי מספר רישוי.
        """
        return (self.make or "").split()[0] if self.make else ""

    def share(self, total):
        """אחוז מכלל הרכבים הפעילים. ללא סה"כ אין למה להשוות."""
        if not total:
            return 0.0
        return self.vehicles * 100.0 / total

    def to_dict(self):
        return {
            "make": self.make,
            "model": self.model,
            "model_code": self.model_code,
            "vehicles": self.vehicles,
            "year_from": self.year_from,
            "year_to": self.year_to,
            "years": self.years,
        }

    def __repr__(self):
        return f"<FleetModelCount {self.make} {self.model} {self.vehicles}>"


# ---- שכבת הרשת ----


def _clean(value):
    return (str(value).strip() if value is not None else "") or None


def _int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _fetch_json(url, timeout):
    request = urllib.request.Request(url, headers={"User-Agent": "makat-catalog/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def sql_page(offset, limit=SQL_PAGE_SIZE, min_count=1, resource_id=None, timeout=TIMEOUT):
    """עמוד אחד של הפילוח, מחושב בצד המאגר.

    min_count ו-limit מומרים ל-int לפני ההשחלה לשאילתה: הם מגיעים משורת
    הפקודה, והם היחידים בשאילתה שאינם קבועים בקוד.
    """
    sql = COUNT_SQL.format(
        make=FIELD_MAKE,
        model=FIELD_MODEL,
        code=FIELD_CODE,
        year=FIELD_YEAR,
        resource=resource_id or RESOURCE_ID,
        min_count=int(min_count),
        limit=int(limit),
        offset=int(offset),
    )
    payload = _fetch_json(
        f"{CKAN_SQL_URL}?{urllib.parse.urlencode({'sql': sql})}", timeout
    )
    if not payload.get("success", True):
        raise ValueError(str(payload.get("error") or "שאילתת SQL נדחתה"))
    return (payload.get("result") or {}).get("records") or []


def scan_page(offset, page_size=SCAN_PAGE_SIZE, resource_id=None, timeout=TIMEOUT):
    """עמוד גולמי של רכבים, לשימוש כשנקודת ה-SQL סגורה.

    מבקש רק את ארבעת השדות שהספירה צריכה - שורה מלאה במאגר הזה היא
    27 שדות, ומשיכה של שלושה מיליון מהן מבזבזת רוחב פס על כלום.
    """
    params = urllib.parse.urlencode(
        {
            "resource_id": resource_id or RESOURCE_ID,
            "limit": page_size,
            "offset": offset,
            "fields": ",".join(SCAN_FIELDS),
        }
    )
    payload = _fetch_json(f"{CKAN_URL}?{params}", timeout)
    result = payload.get("result") or {}
    return result.get("records") or [], result.get("total")


# ---- צבירה ----


def _row(make, model, model_code, vehicles, year_from=None, year_to=None):
    return {
        "make": make or UNKNOWN,
        "model": model or model_code or UNKNOWN,
        "model_code": model_code,
        "vehicles": vehicles,
        "year_from": year_from,
        "year_to": year_to,
    }


def _from_sql(record):
    """רשומת SQL -> שורה. שמות השדות הם הכינויים שבשאילתה."""
    return _row(
        _clean(record.get("make")),
        _clean(record.get("model")),
        _clean(record.get("model_code")),
        _int(record.get("vehicles")) or 0,
        _int(record.get("year_from")),
        _int(record.get("year_to")),
    )


def fetch_counts(min_count=1, page_size=SQL_PAGE_SIZE, fetch=None, max_rows=None,
                 progress=None):
    """הפילוח המלא לפי דגם, בעמודים, מנקודת ה-SQL.

    עוצר על עמוד קצר מהמבוקש - זה הסימן היחיד שהמאגר נותן לכך שנגמרו
    הקבוצות, כי GROUP BY לא מחזיר total.
    """
    fetch = fetch or sql_page
    rows, offset = [], 0
    while True:
        page = fetch(offset, page_size, min_count)
        rows.extend(_from_sql(record) for record in page)
        if progress:
            progress(len(rows))
        if len(page) < page_size:
            break
        offset += len(page)
        if max_rows is not None and len(rows) >= max_rows:
            break
    if max_rows is not None:
        rows = rows[:max_rows]
    return rows


def aggregate_records(records, counts=None):
    """סופר רשומות רכב גולמיות לפי (יצרן, דגם, קוד דגם).

    מקבל ומחזיר את מצב הספירה, כדי שאפשר יהיה לצבור עמוד אחרי עמוד בלי
    להחזיק שלושה מיליון שורות בזיכרון.
    """
    counts = {} if counts is None else counts
    for record in records:
        make = _clean(record.get(FIELD_MAKE))
        model = _clean(record.get(FIELD_MODEL))
        code = _clean(record.get(FIELD_CODE))
        if not (make or model or code):
            continue
        key = (make or UNKNOWN, model or code or UNKNOWN, code)
        entry = counts.get(key)
        if entry is None:
            entry = counts[key] = _row(key[0], key[1], code, 0)
        entry["vehicles"] += 1

        year = _int(record.get(FIELD_YEAR))
        if year:
            if entry["year_from"] is None or year < entry["year_from"]:
                entry["year_from"] = year
            if entry["year_to"] is None or year > entry["year_to"]:
                entry["year_to"] = year
    return counts


def sort_rows(rows, min_count=1):
    """הגדול ראשון. זה הסדר שבו קוראים את הטבלה הזאת בפועל."""
    kept = [row for row in rows if row["vehicles"] >= min_count]
    return sorted(kept, key=lambda row: (-row["vehicles"], row["make"], row["model"]))


def scan_counts(page_size=SCAN_PAGE_SIZE, fetch=None, max_records=None, min_count=1,
                progress=None):
    """אותו פילוח, בדפדוף מקומי. איטי - שלוש מיליון שורות באלפים."""
    fetch = fetch or scan_page
    counts, offset, seen = {}, 0, 0
    while True:
        page, total = fetch(offset, page_size)
        if not page:
            break
        aggregate_records(page, counts)
        seen += len(page)
        offset += len(page)
        if progress:
            progress(seen, total)
        if total and offset >= total:
            break
        if max_records is not None and seen >= max_records:
            break
    return sort_rows(counts.values(), min_count)


# ---- צילום המצב במסד ----

_SNAPSHOT_FIELDS = ("make", "model", "model_code", "vehicles", "year_from", "year_to")


def replace_snapshot(rows, taken_at=None):
    """מחליף את צילום המצב כולו בטרנזקציה אחת."""
    taken_at = taken_at or _now()
    payload = [
        {field: row.get(field) for field in _SNAPSHOT_FIELDS} | {"taken_at": taken_at}
        for row in rows
    ]
    FleetModelCount.query.delete()
    if payload:
        db.session.bulk_insert_mappings(FleetModelCount, payload)
    db.session.commit()
    return len(payload)


def summary():
    """סה"כ רכבים, מספר הדגמים ותאריך הצילום. ריק = עוד לא נטען."""
    row = db.session.query(
        db.func.sum(FleetModelCount.vehicles),
        db.func.count(FleetModelCount.id),
        db.func.max(FleetModelCount.taken_at),
    ).one()
    return {"vehicles": row[0] or 0, "models": row[1] or 0, "taken_at": row[2]}


def _filtered(query, q=None, make=None):
    """הסינון עצמו, בלי מיון - כדי שגם הסכימה תוכל להשתמש בו."""
    if make:
        query = query.filter(FleetModelCount.make == make)
    if q:
        pattern = f"%{q}%"
        query = query.filter(
            db.or_(
                FleetModelCount.model.ilike(pattern),
                FleetModelCount.make.ilike(pattern),
                FleetModelCount.model_code.ilike(pattern),
            )
        )
    return query


def search(q=None, make=None):
    """שאילתת הטבלה, מהדגם הנפוץ לנדיר."""
    return _filtered(FleetModelCount.query, q, make).order_by(
        FleetModelCount.vehicles.desc(), FleetModelCount.make, FleetModelCount.model
    )


def total_vehicles(q=None, make=None):
    """כמה רכבים יש בסינון הנוכחי - לא כמה שורות, כמה רכבים."""
    query = _filtered(
        db.session.query(db.func.sum(FleetModelCount.vehicles)), q, make
    )
    return query.scalar() or 0


# ---- ייצוא ----

CSV_COLUMNS = ("make", "model", "model_code", "vehicles", "year_from", "year_to")
CSV_HEADER = ("יצרן", "דגם", "קוד דגם", "רכבים פעילים", "משנת", "עד שנת")


def to_csv(rows):
    """שורות הפילוח כ-CSV, עם BOM כדי שאקסל יציג את העברית נכון.

    מימוש אחד לסקריפט ולמסך גם יחד - הקובץ שיורד מהדפדפן והקובץ
    שהסקריפט כותב חייבים להיות אותו קובץ.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_HEADER)
    for row in rows:
        writer.writerow([row.get(column) if row.get(column) is not None else ""
                         for column in CSV_COLUMNS])
    return "\ufeff" + buffer.getvalue()


def makes():
    """יצרנים לפי מספר הרכבים שלהם על הכביש."""
    rows = (
        db.session.query(
            FleetModelCount.make, db.func.sum(FleetModelCount.vehicles).label("total")
        )
        .group_by(FleetModelCount.make)
        .order_by(db.desc("total"))
        .all()
    )
    return [row[0] for row in rows]
