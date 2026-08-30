"""ספירת הצי מתוך היישום, במנות.

הסקריפט scripts/vehicle_stats.py מיועד למי שיושב מול טרמינל. בפרודקשן אין
טרמינל כזה, ולכן אותה ספירה בדיוק רצה גם מהדפדפן - מסך /admin/fleet-stats.

העבודה מחולקת למנות מאותה סיבה שייבוא קטלוג הדגמים מחולק: gunicorn הורג
בקשה אחרי 60 שניות, וספירה של שלושה מיליון רשומות אצל המאגר מוגשת בעמודים.
נקודת ההמשך יושבת ב-FleetStatsJob ולא בזיכרון - כמה workers, נפילה באמצע
וסגירת דפדפן.

מה שמיוחד לספירה: היא בונה צילום חדש לצד הישן ולא במקומו. כל שורה נושאת את
חותמת ההרצה, המסך ממשיך להציג את הצילום השלם הקודם, וההחלפה קורית ברגע אחד
בסוף. חצי ספירה שמוצגת כאילו היא הצי כולו היא מספר שקרי, לא מספר חלקי.
"""
import time
from datetime import datetime, timezone

from flask import current_app

from . import fleet_stats
from .fleet_stats import FleetStatsJob, active_job, latest_job, sql_page
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


def run_chunk(job, pages=None, time_budget=None, fetch=None):
    """מריץ מנה אחת: מושך כמה עמודים, כותב אותם ומקדם את נקודת ההמשך."""
    if not job.is_running:
        return job

    config = current_app.config
    page_size = config["FLEET_STATS_PAGE_SIZE"]
    pages = pages or config["FLEET_STATS_PAGES_PER_CHUNK"]
    time_budget = time_budget or config["FLEET_STATS_TIME_BUDGET"]
    attempts = config["FLEET_STATS_FETCH_ATTEMPTS"]
    retry_pause = config["FLEET_STATS_RETRY_PAUSE"]
    page_pause = config["FLEET_STATS_PAGE_PAUSE"]
    fetch = fetch or (lambda offset: sql_page(offset, page_size, MIN_COUNT))
    deadline = time.monotonic() + time_budget

    records, pages_done, exhausted, error = [], 0, False, None
    while pages_done < pages:
        offset = job.offset + len(records)
        if pages_done and page_pause:
            time.sleep(page_pause)  # לא מציפים את השרת הממשלתי
        try:
            page = fetch_with_retry(fetch, offset, attempts, retry_pause)
        except NETWORK_ERRORS as exc:
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

    job.error = error
    job.updated_at = _now()

    if exhausted and not job.models:
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
