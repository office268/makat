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
import base64
import csv
import io
import json
import zlib
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

# חלון האפטרמרקט: רכב חדש עדיין באחריות ובטיפולי סוכנות, ורכב ישן מאוד
# כבר בקושי מטופל. מה שביניהם הוא הקהל שקונה חלפים - ולכן הצילום שומר
# את שלוש הקבוצות בנפרד ולא רק את הסכום.
PRIME_FROM_AGE = 4
PRIME_TO_AGE = 12

# הדליים מושווים כטקסט ולא כמספר: שנת ייצור היא ארבע ספרות, ההשוואה
# הלקסיקוגרפית נכונה עליהן, והמרה מפורשת ל-int הייתה מפילה את כל
# השאילתה על שורה בודדת עם ערך פגום.
COUNT_SQL = (
    'SELECT "{make}" AS make, "{model}" AS model, "{code}" AS model_code, '
    "count(*) AS vehicles, "
    "sum(case when \"{year}\"::text > '{young_after}' then 1 else 0 end) AS young, "
    "sum(case when \"{year}\"::text <= '{young_after}' "
    "and \"{year}\"::text >= '{prime_from}' then 1 else 0 end) AS prime, "
    "sum(case when \"{year}\"::text < '{prime_from}' then 1 else 0 end) AS old, "
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
    # פילוח הגיל: עד 3 שנים, 4-12 (חלון האפטרמרקט), ומעליו. שלושתם יחד
    # יכולים להיות פחות מ-vehicles - רשומה בלי שנת ייצור אינה בשום דלי.
    young = db.Column(db.Integer, default=0, nullable=False)
    prime = db.Column(db.Integer, default=0, nullable=False, index=True)
    old = db.Column(db.Integer, default=0, nullable=False)
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

    @property
    def prime_share(self):
        """איזה חלק מהדגם נמצא בחלון האפטרמרקט."""
        if not self.vehicles:
            return 0.0
        return (self.prime or 0) * 100.0 / self.vehicles

    def vehicles_per_part(self, parts):
        """כמה רכבים בטווח הקנייה על כל מק"ט שיש לנו לדגם.

        בלי מק"טים בכלל אין מכנה, והתשובה אינה "אפס" אלא "הכל" - הפער
        הגדול ביותר שיכול להיות. מוחזר None, והמסך מציג זאת במפורש.
        """
        if not parts:
            return None
        return (self.prime or 0) / parts

    def to_dict(self):
        return {
            "make": self.make,
            "model": self.model,
            "model_code": self.model_code,
            "vehicles": self.vehicles,
            "young": self.young,
            "prime": self.prime,
            "old": self.old,
            "year_from": self.year_from,
            "year_to": self.year_to,
            "years": self.years,
        }

    def __repr__(self):
        return f"<FleetModelCount {self.make} {self.model} {self.vehicles}>"


class FleetStatsJob(db.Model):
    """הרצת ספירה אחת, עם נקודת ההמשך שלה והחותמת של הצילום שהיא בונה.

    אותו היגיון כמו ייבוא קטלוג הדגמים: המסך מריץ מנות, וההתקדמות יושבת
    ב-DB ולא בזיכרון - כמה workers, נפילה באמצע, וסגירת דפדפן.

    מה שנוסף כאן הוא snapshot_at: כל שורה שההרצה כותבת נושאת אותה, ולכן
    הספירה החדשה נבנית לצד הישנה בלי לגעת בה. הצילום הישן ממשיך להיות
    זה שמוצג עד שהחדש שלם, ורק אז הוא נמחק. חצי ספירה שמוצגת כאילו היא
    הצי כולו היא מספר שקרי, לא מספר חלקי.
    """

    __tablename__ = "fleet_stats_jobs"

    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"

    # שני מסלולים לאותה תוצאה: המאגר סופר בעצמו, או שאנחנו סורקים וסופרים
    SQL = "sql"
    SCAN = "scan"
    MODE_LABELS = {SQL: "ספירה אצל המאגר", SCAN: "סריקה מלאה"}

    STATUS_LABELS = {
        RUNNING: "בתהליך",
        DONE: "הושלם",
        FAILED: "נכשל",
        CANCELLED: "בוטל",
    }

    id = db.Column(db.Integer, primary_key=True)
    status = db.Column(db.String(20), default=RUNNING, nullable=False, index=True)
    offset = db.Column(db.Integer, default=0, nullable=False)   # העמוד הבא למשיכה
    models = db.Column(db.Integer, default=0, nullable=False)   # דגמים שנספרו
    vehicles = db.Column(db.Integer, default=0, nullable=False)  # רכבים שנספרו
    error = db.Column(db.Text)
    failures = db.Column(db.Integer, default=0, nullable=False)  # כשלונות רצופים
    mode = db.Column(db.String(10), default=SQL, nullable=False)
    total = db.Column(db.Integer)   # סה"כ רשומות במאגר, בסריקה בלבד
    # מצב הספירה בזמן סריקה: מיליוני שורות מתכווצות לעשרות אלפי דגמים,
    # והן חייבות להיצבר בין בקשה לבקשה. שדה אחד שנקרא ונכתב פעם אחת למנה,
    # במקום מיליון שורות ביניים בטבלה
    counts = db.Column(db.Text)
    snapshot_at = db.Column(db.DateTime, nullable=False, default=_now, index=True)
    started_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    started_at = db.Column(db.DateTime, default=_now)
    updated_at = db.Column(db.DateTime, default=_now)
    finished_at = db.Column(db.DateTime)

    started_by = db.relationship("User", foreign_keys=[started_by_id])

    @property
    def is_running(self):
        return self.status == self.RUNNING

    @property
    def status_label(self):
        return self.STATUS_LABELS.get(self.status, self.status)

    @property
    def mode_label(self):
        return self.MODE_LABELS.get(self.mode, self.mode)

    @property
    def progress_pct(self):
        """אחוז אמיתי רק בסריקה, שבה המאגר מוסר סה"כ רשומות.

        בספירה אצל המאגר אין למה להשוות - GROUP BY לא מחזיר כמה קבוצות
        יהיו - ולכן שם אין אחוז, ולא ממציאים אחד.
        """
        if self.mode != self.SCAN or not self.total:
            return None
        return min(100, round(self.offset * 100 / self.total))

    @property
    def action_label(self):
        """מה הכפתור עושה בפועל מהמצב הנוכחי.

        אין למאגר "סה\"כ קבוצות" להשוות אליו, ולכן אין אחוז התקדמות ואי
        אפשר להסתמך עליו כדי להסביר את הכפתור - הטקסט הוא ההסבר.

        הרצה שנפלה על העמוד הראשון לא השאירה מה להמשיך, ו"המשך" הוא
        תיאור שגוי של מה שיקרה בלחיצה - שם היא מתחילה מאפס, וכך היא
        גם אומרת.
        """
        if self.status == self.DONE:
            return "ספירה מחדש"
        if self.offset or self.models:
            return "המשך ספירה"
        return "התחל ספירה"

    def to_dict(self):
        return {
            "id": self.id,
            "status": self.status,
            "status_label": self.status_label,
            "action_label": self.action_label,
            "offset": self.offset,
            "models": self.models,
            "vehicles": self.vehicles,
            "error": self.error,
            "failures": self.failures,
            "is_running": self.is_running,
            "mode": self.mode,
            "mode_label": self.mode_label,
            "total": self.total,
            "progress_pct": self.progress_pct,
        }

    def __repr__(self):
        return f"<FleetStatsJob {self.id} {self.status} @{self.offset}>"


def active_job():
    """ההרצה הפתוחה, אם יש. שתיים במקביל היו בונות שני צילומים חלקיים."""
    return (
        FleetStatsJob.query.filter_by(status=FleetStatsJob.RUNNING)
        .order_by(FleetStatsJob.id.desc())
        .first()
    )


def latest_job():
    return FleetStatsJob.query.order_by(FleetStatsJob.id.desc()).first()



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
    this_year = _now().year
    sql = COUNT_SQL.format(
        make=FIELD_MAKE,
        model=FIELD_MODEL,
        code=FIELD_CODE,
        year=FIELD_YEAR,
        young_after=this_year - PRIME_FROM_AGE,   # שנת ייצור גדולה מזו = חדש
        prime_from=this_year - PRIME_TO_AGE,      # וקטנה מזו = ישן
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

    sort=_id אינו קישוט: בלי סדר מוגדר, offset בבקשה אחת לא מצביע על
    אותו מקום כמו בבקשה הקודמת, וסריקה של תשעים עמודים הייתה סופרת
    רכבים פעמיים ומדלגת על אחרים.
    """
    params = urllib.parse.urlencode(
        {
            "resource_id": resource_id or RESOURCE_ID,
            "limit": page_size,
            "offset": offset,
            "fields": ",".join(SCAN_FIELDS),
            "sort": "_id",
        }
    )
    payload = _fetch_json(f"{CKAN_URL}?{params}", timeout)
    result = payload.get("result") or {}
    return result.get("records") or [], result.get("total")


# ---- צבירה ----


def _row(make, model, model_code, vehicles, year_from=None, year_to=None,
         young=0, prime=0, old=0):
    return {
        "make": make or UNKNOWN,
        "model": model or model_code or UNKNOWN,
        "model_code": model_code,
        "vehicles": vehicles,
        "year_from": year_from,
        "year_to": year_to,
        "young": young,
        "prime": prime,
        "old": old,
    }


def age_bucket(year, this_year=None):
    """לאיזו קבוצת גיל שייך רכב משנת הייצור הזאת. None = שנה לא ידועה."""
    if not year:
        return None
    age = (this_year or _now().year) - year
    if age < PRIME_FROM_AGE:
        return "young"
    if age <= PRIME_TO_AGE:
        return "prime"
    return "old"


def _from_sql(record):
    """רשומת SQL -> שורה. שמות השדות הם הכינויים שבשאילתה."""
    return _row(
        _clean(record.get("make")),
        _clean(record.get("model")),
        _clean(record.get("model_code")),
        _int(record.get("vehicles")) or 0,
        _int(record.get("year_from")),
        _int(record.get("year_to")),
        young=_int(record.get("young")) or 0,
        prime=_int(record.get("prime")) or 0,
        old=_int(record.get("old")) or 0,
    )


def rows_from_sql(records):
    """רשומות מנקודת ה-SQL -> שורות פילוח."""
    return [_from_sql(record) for record in records]


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
        rows.extend(rows_from_sql(page))
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
    # שנה אחת לכל המנה: היא לא משתנה באמצע, וחישוב שלה לכל שורה
    # משלושה מיליון הוא בזבוז
    this_year = _now().year
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
        bucket = age_bucket(year, this_year)
        if bucket:
            entry[bucket] += 1
        if year:
            if entry["year_from"] is None or year < entry["year_from"]:
                entry["year_from"] = year
            if entry["year_to"] is None or year > entry["year_to"]:
                entry["year_to"] = year
    return counts


def pack_counts(counts):
    """מכווץ את מצב הספירה לשדה אחד, לשמירה בין מנה למנה.

    כמה עשרות אלפי דגמים כ-JSON הם כמה מגהבייטים; דחיסה מורידה אותם
    לסדר גודל של מאות קילובייטים, וזה מה שנקרא ונכתב בכל מנה.
    """
    payload = json.dumps(
        [
            [key[0], key[1], key[2], row["vehicles"], row["year_from"], row["year_to"],
             row["young"], row["prime"], row["old"]]
            for key, row in counts.items()
        ],
        ensure_ascii=False,
    )
    return base64.b64encode(zlib.compress(payload.encode("utf-8"), 6)).decode("ascii")


def unpack_counts(blob):
    """מחזיר את מצב הספירה למבנה שאפשר להמשיך לצבור אליו."""
    if not blob:
        return {}
    payload = zlib.decompress(base64.b64decode(blob)).decode("utf-8")
    return {
        (make, model, code): _row(make, model, code, vehicles, year_from, year_to,
                                  young, prime, old)
        for make, model, code, vehicles, year_from, year_to, young, prime, old
        in json.loads(payload)
    }


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

_SNAPSHOT_FIELDS = ("make", "model", "model_code", "vehicles", "year_from",
                    "year_to", "young", "prime", "old")


def add_rows(rows, taken_at):
    """כותב שורות לתוך צילום מסוים. מחזיר (כמה דגמים, כמה רכבים)."""
    payload = [
        {field: row.get(field) for field in _SNAPSHOT_FIELDS} | {"taken_at": taken_at}
        for row in rows
    ]
    if payload:
        db.session.bulk_insert_mappings(FleetModelCount, payload)
    return len(payload), sum(row["vehicles"] or 0 for row in payload)


def publish(taken_at):
    """הופך צילום לזה שמוצג: מוחק כל שורה שאינה שייכת לו.

    זה רגע ההחלפה, והוא בא רק כשהספירה שלמה. עד אליו הצילום הישן
    ממשיך להיענות למסך.
    """
    FleetModelCount.query.filter(FleetModelCount.taken_at != taken_at).delete(
        synchronize_session=False
    )
    db.session.commit()


def discard(taken_at):
    """מוחק צילום חלקי שלא יגיע לכלל שלמות."""
    FleetModelCount.query.filter(FleetModelCount.taken_at == taken_at).delete(
        synchronize_session=False
    )
    db.session.commit()


def replace_snapshot(rows, taken_at=None):
    """מחליף את צילום המצב כולו בטרנזקציה אחת. זה המסלול של הסקריפט."""
    taken_at = taken_at or _now()
    FleetModelCount.query.delete()
    models, _ = add_rows(rows, taken_at)
    db.session.commit()
    return models


def live_taken_at():
    """החותמת של הצילום השלם האחרון - זה שמותר להציג.

    צילום שנבנה כרגע, נכשל או בוטל יושב בטבלה עם חותמת משלו. הצגתו
    הייתה מציגה חצי ספירה כאילו היא הצי כולו, ולכן חותמות של הרצות
    שאינן "הושלם" נשארות מחוץ לתמונה.
    """
    partial = db.session.query(FleetStatsJob.snapshot_at).filter(
        FleetStatsJob.status != FleetStatsJob.DONE,
        FleetStatsJob.snapshot_at.isnot(None),
    )
    return (
        db.session.query(db.func.max(FleetModelCount.taken_at))
        .filter(FleetModelCount.taken_at.notin_(partial))
        .scalar()
    )


def _filtered(query, q=None, make=None, taken_at=None):
    """הסינון עצמו, בלי מיון - כדי שגם הסכימה תוכל להשתמש בו.

    כל שאילתה מוגבלת לצילום אחד. בלי זה, בזמן ספירה חדשה היו נספרים
    שני צילומים יחד וכל מספר על המסך היה כפול.
    """
    if taken_at is None:
        taken_at = live_taken_at()
    # taken_at ריק -> IS NULL, והעמודה אינה nullable: אין נתונים, אין תוצאות
    query = query.filter(FleetModelCount.taken_at == taken_at)
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


def summary(taken_at=None):
    """סה"כ רכבים, מספר הדגמים ותאריך הצילום. ריק = עוד לא נטען."""
    if taken_at is None:
        taken_at = live_taken_at()
    row = _filtered(
        db.session.query(
            db.func.sum(FleetModelCount.vehicles),
            db.func.count(FleetModelCount.id),
            db.func.sum(FleetModelCount.prime),
        ),
        taken_at=taken_at,
    ).one()
    return {
        "vehicles": row[0] or 0,
        "models": row[1] or 0,
        "prime": row[2] or 0,
        "taken_at": taken_at,
    }


SORTS = {
    "vehicles": "רכבים על הכביש",
    "prime": "רכבים בטווח הקנייה",
}


def search(q=None, make=None, taken_at=None, sort="vehicles"):
    """שאילתת הטבלה, מהדגם הגדול לקטן לפי המיון המבוקש."""
    column = FleetModelCount.prime if sort == "prime" else FleetModelCount.vehicles
    return _filtered(FleetModelCount.query, q, make, taken_at).order_by(
        column.desc(), FleetModelCount.make, FleetModelCount.model
    )


def total_vehicles(q=None, make=None, taken_at=None):
    """כמה רכבים יש בסינון הנוכחי - לא כמה שורות, כמה רכבים."""
    return (
        _filtered(
            db.session.query(db.func.sum(FleetModelCount.vehicles)), q, make, taken_at
        ).scalar()
        or 0
    )



# ---- ייצוא ----

CSV_COLUMNS = ("make", "model", "model_code", "vehicles", "young", "prime", "old",
               "year_from", "year_to")
CSV_HEADER = ("יצרן", "דגם", "קוד דגם", "רכבים פעילים", "עד 3 שנים",
              "בטווח הקנייה 4-12", "מעל 12", "משנת", "עד שנת")


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


def makes(taken_at=None):
    """יצרנים לפי מספר הרכבים שלהם על הכביש."""
    rows = (
        _filtered(
            db.session.query(
                FleetModelCount.make,
                db.func.sum(FleetModelCount.vehicles).label("total"),
            ),
            taken_at=taken_at,
        )
        .group_by(FleetModelCount.make)
        .order_by(db.desc("total"))
        .all()
    )
    return [row[0] for row in rows]
