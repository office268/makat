"""משיכת דגמי רכב ממאגר משרד התחבורה ב-data.gov.il, במנות.

המאגר מוגש דרך CKAN datastore_search - עמוד של 1000 רשומות בכל בקשה, מעל
100 אלף רשומות בסך הכל. משיכה של הכל בבקשת HTTP אחת של המשתמש בלתי אפשרית:
gunicorn הורג בקשה אחרי 60 שניות. לכן העבודה מחולקת למנות, וה-offset של
המנה הבאה נשמר ב-VehicleImportJob - כך שהדפדפן יכול להמשיך אוטומטית,
והרצה שנקטעה ממשיכה מהנקודה שנעצרה.

הסקריפט scripts/import_vehicle_models.py משתמש באותה שכבת רשת בדיוק.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from flask import current_app

from .models import db
from .vehicle_catalog import VehicleImportJob, collapse_records, upsert

CKAN_URL = "https://data.gov.il/api/3/action/datastore_search"
RESOURCE_ID = "142afde2-6228-49f9-8a29-9b6c3a0cbe40"
PAGE_SIZE = 1000

# שגיאות שאפשר להתאושש מהן: נשמרות על העבודה והמנה הבאה מנסה שוב מאותו offset
NETWORK_ERRORS = (urllib.error.URLError, TimeoutError, OSError, ValueError)


def fetch_page(offset, page_size=PAGE_SIZE, timeout=30):
    """מושך עמוד אחד מהמאגר. מחזיר (רשומות, סה"כ)."""
    params = urllib.parse.urlencode(
        {"resource_id": RESOURCE_ID, "limit": page_size, "offset": offset}
    )
    request = urllib.request.Request(
        f"{CKAN_URL}?{params}", headers={"User-Agent": "makat-catalog/1.0"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    result = payload.get("result") or {}
    return result.get("records") or [], result.get("total")


def start_job(user_id=None):
    """פותח הרצה חדשה, או מחזיר את הפתוחה אם כבר יש כזו."""
    from .vehicle_catalog import active_job

    existing = active_job()
    if existing is not None:
        return existing
    job = VehicleImportJob(status=VehicleImportJob.RUNNING, started_by_id=user_id)
    db.session.add(job)
    db.session.commit()
    return job


def cancel_job(job):
    if job is not None and job.is_running:
        _finish(job, VehicleImportJob.CANCELLED)
    return job


def _now():
    return datetime.now(timezone.utc)


def _finish(job, status):
    job.status = status
    job.finished_at = _now()
    job.updated_at = _now()
    db.session.commit()


def run_chunk(job, pages=None, time_budget=None, fetch=None):
    """מריץ מנה אחת: מושך כמה עמודים, שומר אותם ומקדם את נקודת ההמשך.

    עוצר מוקדם כשתקציב הזמן נגמר, כדי שהבקשה תסתיים הרבה לפני ה-timeout
    של gunicorn. מחזיר את העבודה המעודכנת.
    """
    if not job.is_running:
        return job

    fetch = fetch or fetch_page
    pages = pages or current_app.config["VEHICLE_IMPORT_PAGES_PER_CHUNK"]
    time_budget = time_budget or current_app.config["VEHICLE_IMPORT_TIME_BUDGET"]
    deadline = time.monotonic() + time_budget

    records, pages_done, exhausted, error = [], 0, False, None
    while pages_done < pages:
        try:
            page, total = fetch(job.offset + len(records))
        except NETWORK_ERRORS as exc:
            # לא מקדמים את ה-offset מעבר למה שנמשך בפועל:
            # המנה הבאה תנסה שוב בדיוק מהעמוד שנפל
            error = f"שגיאת רשת ב-offset {job.offset + len(records)}: {exc}"
            break

        if total is not None:
            job.total = total
        if not page:
            exhausted = True
            break

        records.extend(page)
        pages_done += 1
        if job.total and job.offset + len(records) >= job.total:
            exhausted = True
            break
        if time.monotonic() >= deadline:
            break

    if records:
        # מכווצים את כל רשומות המנה יחד - אותו דגם מופיע בכמה שנות ייצור,
        # ולעיתים גם משני צדי גבול עמוד
        rows = collapse_records(records)
        created, updated = upsert(rows)
        job.created += created
        job.updated += updated
        job.fetched += len(records)
        job.offset += len(records)

    job.error = error
    job.updated_at = _now()
    if exhausted:
        _finish(job, VehicleImportJob.DONE)
    else:
        db.session.commit()
    return job
