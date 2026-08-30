"""ספירת הצי מתוך היישום, במנות.

הסקריפט scripts/vehicle_stats.py מיועד למי שיושב מול טרמינל. בפרודקשן אין
טרמינל כזה, ולכן אותה ספירה בדיוק רצה גם מהדפדפן - מסך /admin/fleet-stats.

העבודה מחולקת למנות מאותה סיבה שייבוא קטלוג הדגמים מחולק: gunicorn הורג
בקשה אחרי 60 שניות, וספירה של שלושה מיליון רשומות אצל המאגר מוגשת בעמודים.
נקודת ההמשך יושבת ב-FleetStatsJob ולא בזיכרון - כמה workers, נפילה באמצע
וסגירת דפדפן.

שני מסלולים לאותה ספירה: המאגר סופר בעצמו ב-GROUP BY אחד, או שאנחנו
מושכים את השורות וסופרים כאן. נקודת ה-SQL של המאגר לא זמינה בכל סביבה -
היא מחזירה 404 - ולכן ההרצה מזהה זאת ועוברת לסריקה בעצמה, בלי שמישהו
יצטרך לדעת שיש שני מסלולים.

מה שמיוחד לספירה: היא בונה צילום חדש לצד הישן ולא במקומו. כל שורה נושאת את
חותמת ההרצה, המסך ממשיך להציג את הצילום השלם הקודם, וההחלפה קורית ברגע אחד
בסוף. חצי ספירה שמוצגת כאילו היא הצי כולו היא מספר שקרי, לא מספר חלקי.
"""
import time
import urllib.error
from datetime import datetime, timezone

from flask import current_app

from . import fleet_stats
from .fleet_stats import FleetStatsJob, active_job, latest_job, scan_page, sql_page
from .models import db
from .vehicle_import import NETWORK_ERRORS, describe_error, fetch_with_retry

# כל הדגמים, גם הנדירים: דגם עם שני רכבים בארץ הוא בדיוק המקרה שבו כדאי
# לדעת שאין טעם להחזיק לו מלאי
MIN_COUNT = 1


def _now():
    return datetime.now(timezone.utc)


def start_job(user_id=None):
    """פותח ספירה, או ממשיך את זו שנעצרה באמצע.

    הרצה שנכשלה או בוטלה ממשיכה מאותו offset ועם אותה חותמת צילום -
    השורות שכבר נכתבו נשארות, ואחרת כל לחיצה על "המשך" הייתה סופרת
    מהתחלה שלושה מיליון רכבים.
    """
    existing = active_job()
    if existing is not None:
        return existing

    previous = latest_job()
    if previous is not None and previous.status in (
        FleetStatsJob.FAILED,
        FleetStatsJob.CANCELLED,
    ):
        previous.status = FleetStatsJob.RUNNING
        previous.error = None
        previous.failures = 0
        previous.finished_at = None
        previous.updated_at = _now()
        db.session.commit()
        return previous

    job = FleetStatsJob(
        status=FleetStatsJob.RUNNING, started_by_id=user_id, snapshot_at=_now()
    )
    db.session.add(job)
    db.session.commit()
    return job


def cancel_job(job):
    """עוצר ספירה ומשאיר את מה שכבר נספר.

    השורות החלקיות אינן מוצגות (החותמת שלהן שייכת להרצה שלא הושלמה),
    והן נמחקות מעצמן כשצילום שלם הבא יתפרסם. עד אז הן נקודת ההמשך.
    """
    if job is not None and job.is_running:
        _finish(job, FleetStatsJob.CANCELLED)
    return job


def _finish(job, status, error=None):
    if status == FleetStatsJob.DONE:
        # רגע ההחלפה: מכאן הצילום החדש הוא זה שהמסך מציג
        fleet_stats.publish(job.snapshot_at)
    job.status = status
    if error:
        job.error = error
    job.finished_at = _now()
    job.updated_at = _now()
    db.session.commit()


def _sql_is_missing(exc):
    """האם השגיאה אומרת "נקודת ה-SQL הזאת לא קיימת כאן".

    404 ו-405 הם התשובות של שער שלא מכיר את הנתיב; שגיאה זמנית (500,
    timeout) אינה כזאת, ועליה מנסים שוב במקום להחליף מסלול.
    """
    return isinstance(exc, urllib.error.HTTPError) and exc.code in (403, 404, 405)


def _switch_to_scan(job):
    """עובר מספירה אצל המאגר לסריקה מלאה, ומתחיל את הצילום מחדש.

    השורות שנכתבו עד כה שייכות לספירה שלא תושלם, והן נמחקות: המשך
    סריקה על גביהן היה סופר חלק מהרכבים פעמיים.
    """
    fleet_stats.discard(job.snapshot_at)
    job.mode = FleetStatsJob.SCAN
    job.offset = 0
    job.models = 0
    job.vehicles = 0
    job.counts = None
    job.failures = 0
    job.error = (
        "נקודת ה-SQL של המאגר אינה זמינה. ממשיכים בסריקה מלאה - "
        "אותה תוצאה, לוקח יותר זמן."
    )
    job.updated_at = _now()
    db.session.commit()
    return job


def run_chunk(job, pages=None, time_budget=None, fetch=None):
    """מריץ מנה אחת, לפי המסלול שההרצה נמצאת בו."""
    if not job.is_running:
        return job

    config = current_app.config
    pages = pages or config["FLEET_STATS_PAGES_PER_CHUNK"]
    deadline = time.monotonic() + (time_budget or config["FLEET_STATS_TIME_BUDGET"])
    if job.mode == FleetStatsJob.SCAN:
        return _run_scan_chunk(job, pages, deadline, fetch, config)
    return _run_sql_chunk(job, pages, deadline, fetch, config)


def _run_sql_chunk(job, pages, deadline, fetch, config):
    """המסלול המהיר: המאגר מחזיר את הפילוח מוכן, עמוד אחרי עמוד."""
    page_size = config["FLEET_STATS_PAGE_SIZE"]
    fetch = fetch or (lambda offset: sql_page(offset, page_size, MIN_COUNT))
    page_pause = config["FLEET_STATS_PAGE_PAUSE"]

    records, pages_done, exhausted, error = [], 0, False, None
    while pages_done < pages:
        offset = job.offset + len(records)
        if pages_done and page_pause:
            time.sleep(page_pause)  # לא מציפים את השרת הממשלתי
        try:
            page = fetch_with_retry(
                fetch, offset, config["FLEET_STATS_FETCH_ATTEMPTS"],
                config["FLEET_STATS_RETRY_PAUSE"],
            )
        except NETWORK_ERRORS as exc:
            if _sql_is_missing(exc):
                # לא כישלון של ההרצה אלא של המסלול: אותה ספירה נמשכת בדרך השנייה
                return _switch_to_scan(job)
            # ה-offset לא זז מעבר למה שנמשך בפועל: המנה הבאה תנסה שוב
            # בדיוק מהעמוד שנפל
            error = f"שגיאה ב-offset {offset}: {describe_error(exc)}"
            break

        records.extend(page)
        pages_done += 1
        # אין למאגר "סה"כ קבוצות" להשוות אליו, ולכן עמוד קצר מהמבוקש הוא
        # הסימן היחיד שהספירה הגיעה לסופה
        if len(page) < page_size:
            exhausted = True
            break
        if time.monotonic() >= deadline:
            break

    if records:
        models, vehicles = fleet_stats.add_rows(
            fleet_stats.rows_from_sql(records), job.snapshot_at
        )
        job.models += models
        job.vehicles += vehicles
        job.offset += len(records)
        job.failures = 0
    elif error:
        job.failures = (job.failures or 0) + 1

    return _close_chunk(job, error, exhausted, bool(job.models), config)


def _run_scan_chunk(job, pages, deadline, fetch, config):
    """המסלול השני: מושכים את השורות עצמן וסופרים כאן.

    הצבירה נשמרת על ההרצה ולא בטבלה: שלושה מיליון שורות מתכווצות לעשרות
    אלפי דגמים, וכתיבתן כשורות ביניים הייתה עולה יותר מהספירה עצמה.
    """
    page_size = config["FLEET_STATS_SCAN_PAGE_SIZE"]
    fetch = fetch or (lambda offset: scan_page(offset, page_size))
    page_pause = config["FLEET_STATS_PAGE_PAUSE"]

    counts = fleet_stats.unpack_counts(job.counts)
    seen, pages_done, exhausted, error = 0, 0, False, None
    while pages_done < pages:
        offset = job.offset + seen
        if pages_done and page_pause:
            time.sleep(page_pause)
        try:
            page, total = fetch_with_retry(
                fetch, offset, config["FLEET_STATS_FETCH_ATTEMPTS"],
                config["FLEET_STATS_RETRY_PAUSE"],
            )
        except NETWORK_ERRORS as exc:
            error = f"שגיאה ב-offset {offset}: {describe_error(exc)}"
            break

        if total is not None:
            job.total = total
        if not page:
            exhausted = True
            break

        fleet_stats.aggregate_records(page, counts)
        seen += len(page)
        pages_done += 1
        # כאן יש סה"כ אמיתי מהמאגר, והוא הסימן לסוף - לא אורך העמוד,
        # שהשרת רשאי לקצר מתחת למבוקש
        if job.total and job.offset + seen >= job.total:
            exhausted = True
            break
        if time.monotonic() >= deadline:
            break

    if seen:
        job.counts = fleet_stats.pack_counts(counts)
        job.offset += seen
        job.models = len(counts)
        job.vehicles = sum(row["vehicles"] for row in counts.values())
        job.failures = 0
    elif error:
        job.failures = (job.failures or 0) + 1

    if exhausted and counts:
        # רק כאן, בסוף, הספירה הופכת לשורות בטבלה
        rows = fleet_stats.sort_rows(counts.values())
        fleet_stats.add_rows(rows, job.snapshot_at)
        job.models = len(rows)
        job.vehicles = sum(row["vehicles"] for row in rows)
        job.counts = None

    return _close_chunk(job, error, exhausted, bool(counts), config)


def _close_chunk(job, error, exhausted, has_data, config):
    """סוגר מנה: מעדכן, ומחליט אם ההרצה נגמרה, נכשלה או ממשיכה."""
    job.error = error
    job.updated_at = _now()

    if exhausted and not has_data:
        # ספירה שהסתיימה בלי שורה אחת אינה "צילום ריק" אלא כשל: פרסום
        # שלה היה מוחק את הצילום הקודם ומשאיר את המסך בלי כלום
        _finish(job, FleetStatsJob.FAILED, error="המאגר לא החזיר נתונים.")
    elif exhausted:
        _finish(job, FleetStatsJob.DONE)
    elif job.failures >= config["FLEET_STATS_MAX_FAILURES"]:
        # בלי העצירה הזו הדפדפן היה מנסה שוב לנצח: ה-offset לא מתקדם,
        # הסטטוס נשאר "בתהליך", והלולאה בצד הלקוח לא נגמרת לעולם
        _finish(job, FleetStatsJob.FAILED)
    else:
        db.session.commit()
    return job
