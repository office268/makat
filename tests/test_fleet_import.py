"""ספירת הצי מתוך היישום, במנות: מנות, החלפת צילום, כשלים והמסך."""
import urllib.error

import pytest

from app import fleet_import, fleet_stats
from app.auth_models import Organization, User
from app.fleet_stats import FleetModelCount, FleetStatsJob
from app.models import db

SUPERADMIN = "boss@t.test"


def sql_record(model, vehicles, make="טויוטה יפן", code="A1"):
    return {"make": make, "model": model, "model_code": code,
            "vehicles": vehicles, "year_from": 2015, "year_to": 2022}


def raw(model, year, make="טויוטה יפן", code="A1"):
    """שורת רכב גולמית מהמאגר - אחת לכל רכב פעיל."""
    return {fleet_stats.FIELD_MAKE: make, fleet_stats.FIELD_MODEL: model,
            fleet_stats.FIELD_CODE: code, fleet_stats.FIELD_YEAR: year}


def scanner(pages, total=None):
    """scan_page מזויף: מחזיר (עמוד, סה"כ) לפי ה-offset המבוקש."""
    flat = [row for page in pages for row in page]
    total = len(flat) if total is None else total
    sizes = [len(page) for page in pages]

    def fetch(offset):
        position = 0
        for index, size in enumerate(sizes):
            if position == offset:
                return pages[index], total
            position += size
        return [], total

    return fetch


def not_found(offset):
    raise urllib.error.HTTPError("u", 404, "Not Found", {}, None)


def pager(pages):
    """sql_page מזויף שמגיש עמוד לפי ה-offset המבוקש."""
    sizes = [len(page) for page in pages]

    def fetch(offset):
        position = 0
        for index, size in enumerate(sizes):
            if position == offset:
                return pages[index]
            position += size
        return []

    return fetch


def old_snapshot(rows=(("COROLLA", 1000),)):
    """צילום שלם קיים, כמו זה שהמסך מציג לפני שמתחילים ספירה חדשה."""
    return fleet_stats.replace_snapshot(
        [{"make": "טויוטה יפן", "model": model, "model_code": "A1",
          "vehicles": vehicles} for model, vehicles in rows]
    )


# ---- מנות ----


def test_chunk_advances_and_finishes_on_a_short_page(app):
    pages = [[sql_record("COROLLA", 90000)] * 2, [sql_record("YARIS", 30000)]]
    with app.app_context():
        app.config["FLEET_STATS_PAGE_SIZE"] = 2
        job = fleet_import.start_job()

        fleet_import.run_chunk(job, pages=1, fetch=pager(pages))
        assert job.offset == 2
        assert job.status == FleetStatsJob.RUNNING

        fleet_import.run_chunk(job, pages=1, fetch=pager(pages))
        assert job.status == FleetStatsJob.DONE
        assert job.models == 3
        assert job.vehicles == 90000 * 2 + 30000


def test_the_old_snapshot_stays_live_until_the_new_one_is_whole(app):
    """המספר שעל המסך חייב להיות צי שלם, לא חצי ספירה."""
    pages = [[sql_record("COROLLA", 90000), sql_record("YARIS", 30000)],
             [sql_record("PICANTO", 20000)]]
    with app.app_context():
        old_snapshot()
        app.config["FLEET_STATS_PAGE_SIZE"] = 2
        job = fleet_import.start_job()

        fleet_import.run_chunk(job, pages=1, fetch=pager(pages))
        assert fleet_stats.summary()["vehicles"] == 1000  # עדיין הישן
        assert FleetModelCount.query.count() == 3  # שני הצילומים יחד בטבלה

        fleet_import.run_chunk(job, pages=1, fetch=pager(pages))
        assert job.status == FleetStatsJob.DONE
        assert fleet_stats.summary()["vehicles"] == 140000
        assert FleetModelCount.query.count() == 3  # הישן נמחק בפרסום


def test_partial_snapshot_of_a_stopped_job_is_not_shown(app):
    pages = [[sql_record("COROLLA", 90000)], [sql_record("YARIS", 30000)]]
    with app.app_context():
        old_snapshot()
        app.config["FLEET_STATS_PAGE_SIZE"] = 1
        job = fleet_import.start_job()
        fleet_import.run_chunk(job, pages=1, fetch=pager(pages))
        fleet_import.cancel_job(job)

        assert job.status == FleetStatsJob.CANCELLED
        assert fleet_stats.summary()["vehicles"] == 1000
        assert [row.model for row in fleet_stats.search().all()] == ["COROLLA"]


def test_cancelled_job_resumes_from_where_it_stopped(app):
    pages = [[sql_record("COROLLA", 90000), sql_record("YARIS", 30000)],
             [sql_record("PICANTO", 20000)]]
    with app.app_context():
        app.config["FLEET_STATS_PAGE_SIZE"] = 2
        job = fleet_import.start_job()
        fleet_import.run_chunk(job, pages=1, fetch=pager(pages))
        fleet_import.cancel_job(job)

        resumed = fleet_import.start_job()
        assert resumed.id == job.id
        assert resumed.offset == 2  # השורות שכבר נספרו לא נספרות שוב
        fleet_import.run_chunk(resumed, pages=1, fetch=pager(pages))
        assert resumed.status == FleetStatsJob.DONE
        assert fleet_stats.summary()["vehicles"] == 140000
        assert fleet_stats.summary()["models"] == 3


def test_finished_job_starts_a_fresh_count(app):
    with app.app_context():
        job = fleet_import.start_job()
        job.offset = 40000
        fleet_import._finish(job, FleetStatsJob.DONE)
        fresh = fleet_import.start_job()
        assert fresh.id != job.id
        assert fresh.offset == 0
        assert fresh.snapshot_at != job.snapshot_at


def test_empty_first_page_fails_instead_of_wiping_the_snapshot(app):
    """מאגר שמחזיר כלום אינו 'צי ריק' - פרסום כזה היה מוחק את מה שיש."""
    with app.app_context():
        old_snapshot()
        job = fleet_import.start_job()
        fleet_import.run_chunk(job, pages=1, fetch=lambda offset: [])

        assert job.status == FleetStatsJob.FAILED
        assert "לא החזיר נתונים" in job.error
        assert fleet_stats.summary()["vehicles"] == 1000


def test_network_error_keeps_the_offset(app):
    pages = [[sql_record("COROLLA", 90000), sql_record("YARIS", 30000)],
             [sql_record("PICANTO", 20000)]]
    good = pager(pages)
    calls = {"n": 0}

    def flaky(offset):
        calls["n"] += 1
        if calls["n"] <= app.config["FLEET_STATS_FETCH_ATTEMPTS"]:
            raise urllib.error.URLError("connection refused")
        return good(offset)

    with app.app_context():
        app.config["FLEET_STATS_PAGE_SIZE"] = 2
        job = fleet_import.start_job()
        fleet_import.run_chunk(job, pages=1, fetch=flaky)
        assert job.offset == 0
        assert job.status == FleetStatsJob.RUNNING
        assert "connection refused" in job.error
        assert FleetModelCount.query.count() == 0

        # ההמשך מצליח מאותה נקודה בדיוק ומנקה את השגיאה
        fleet_import.run_chunk(job, pages=2, fetch=good)
        assert job.status == FleetStatsJob.DONE
        assert job.error is None
        assert fleet_stats.summary()["models"] == 3


def test_closed_sql_endpoint_stops_the_job(app):
    """נקודת ה-SQL מחזירה success=false; בלי עצירה הדפדפן מנסה לנצח."""
    def refused(offset):
        raise ValueError("SQL disabled")

    with app.app_context():
        job = fleet_import.start_job()
        limit = app.config["FLEET_STATS_MAX_FAILURES"]
        for expected in range(1, limit + 1):
            fleet_import.run_chunk(job, pages=1, fetch=refused)
            assert job.failures == expected
        assert job.status == FleetStatsJob.FAILED
        assert "SQL disabled" in job.error


def test_cancelled_job_does_not_run(app):
    with app.app_context():
        job = fleet_import.start_job()
        fleet_import.cancel_job(job)
        fleet_import.run_chunk(job, pages=1, fetch=pager([[sql_record("X", 5)]]))
        assert FleetModelCount.query.count() == 0


def test_button_label_says_what_it_will_do(app):
    with app.app_context():
        job = fleet_import.start_job()
        # נפלה על העמוד הראשון: אין מה להמשיך, והכפתור לא מבטיח המשך
        fleet_import._finish(job, FleetStatsJob.FAILED, error="HTTP 404 Not Found")
        assert job.action_label == "התחל ספירה"

        job.offset = 32000
        assert job.action_label == "המשך ספירה"

        fleet_import._finish(job, FleetStatsJob.DONE)
        assert job.action_label == "ספירה מחדש"


# ---- מסלול הסריקה ----


def test_missing_sql_endpoint_switches_to_scanning(app):
    """מה שקרה בייצור: נקודת ה-SQL של המאגר מחזירה 404.

    זה כשל של המסלול, לא של ההרצה - היא ממשיכה בדרך השנייה במקום להיעצר.
    """
    with app.app_context():
        job = fleet_import.start_job()
        assert job.mode == FleetStatsJob.SQL

        fleet_import.run_chunk(job, pages=1, fetch=not_found)
        assert job.mode == FleetStatsJob.SCAN
        assert job.status == FleetStatsJob.RUNNING
        assert job.failures == 0  # לא כישלון שסופרים לקראת עצירה
        assert "סריקה מלאה" in job.error


def test_switch_to_scan_drops_what_the_sql_path_wrote(app):
    """המשך סריקה על שורות מהמסלול הקודם היה סופר רכבים פעמיים."""
    with app.app_context():
        app.config["FLEET_STATS_PAGE_SIZE"] = 2
        job = fleet_import.start_job()
        fleet_import.run_chunk(job, pages=1, fetch=pager(
            [[sql_record("COROLLA", 90000), sql_record("YARIS", 30000)]]))
        assert FleetModelCount.query.count() == 2

        fleet_import.run_chunk(job, pages=1, fetch=not_found)
        assert FleetModelCount.query.count() == 0
        assert (job.offset, job.models, job.vehicles) == (0, 0, 0)


def test_a_temporary_error_does_not_switch_paths(app):
    """500 היא תקלה רגעית; החלפת מסלול בגללה הייתה מוותרת על המהיר."""
    def server_error(offset):
        raise urllib.error.HTTPError("u", 500, "Server Error", {}, None)

    with app.app_context():
        job = fleet_import.start_job()
        fleet_import.run_chunk(job, pages=1, fetch=server_error)
        assert job.mode == FleetStatsJob.SQL
        assert job.failures == 1


def test_scan_accumulates_across_chunks_and_publishes_at_the_end(app):
    pages = [[raw("COROLLA", 2015), raw("COROLLA", 2019)],
             [raw("3", 2018, make="מאזדה יפן", code="B2")]]
    with app.app_context():
        old_snapshot()
        job = fleet_import.start_job()
        job.mode = FleetStatsJob.SCAN
        db.session.commit()

        fleet_import.run_chunk(job, pages=1, fetch=scanner(pages))
        assert job.offset == 2
        assert job.models == 1        # שתי שנות ייצור של אותו דגם
        assert job.vehicles == 2
        assert job.counts             # הצבירה נשמרה בין המנות
        assert fleet_stats.summary()["vehicles"] == 1000  # עדיין הישן
        assert FleetModelCount.query.count() == 1  # שום שורת ביניים לא נכתבה

        fleet_import.run_chunk(job, pages=1, fetch=scanner(pages))
        assert job.status == FleetStatsJob.DONE
        assert job.counts is None
        assert fleet_stats.summary()["vehicles"] == 3
        rows = fleet_stats.search().all()
        assert [(row.model, row.vehicles) for row in rows] == [("COROLLA", 2), ("3", 1)]
        assert rows[0].year_from == 2015 and rows[0].year_to == 2019


def test_scan_progress_uses_the_total_from_the_registry(app):
    """בסריקה יש סה"כ אמיתי, ולכן מותר להראות אחוז."""
    pages = [[raw("COROLLA", 2015)], [raw("YARIS", 2016)]]
    with app.app_context():
        job = fleet_import.start_job()
        job.mode = FleetStatsJob.SCAN
        db.session.commit()
        assert job.progress_pct is None

        fleet_import.run_chunk(job, pages=1, fetch=scanner(pages, total=4))
        assert job.total == 4
        assert job.progress_pct == 25


def test_scan_stops_on_the_total_even_when_the_page_is_short(app):
    """השרת רשאי לקצר עמוד; רק הסה"כ אומר שהסריקה נגמרה."""
    pages = [[raw("COROLLA", 2015)], [raw("YARIS", 2016)]]
    calls = {"n": 0}
    inner = scanner(pages)

    def counting(offset):
        calls["n"] += 1
        return inner(offset)

    with app.app_context():
        job = fleet_import.start_job()
        job.mode = FleetStatsJob.SCAN
        db.session.commit()
        fleet_import.run_chunk(job, pages=5, fetch=counting)
        assert job.status == FleetStatsJob.DONE
        assert calls["n"] == 2  # לא נמשך עמוד מיותר אחרי שהמאגר נגמר


def test_packed_counts_survive_a_round_trip():
    counts = fleet_stats.aggregate_records([raw("COROLLA", 2015), raw("COROLLA", 2019)])
    restored = fleet_stats.unpack_counts(fleet_stats.pack_counts(counts))
    assert restored == counts


def test_html_error_page_is_not_pasted_into_the_message():
    """404 של השער הממשלתי מגיע עם דף HTML שלם - הוא הסתיר את הקוד."""
    from app.vehicle_import import describe_error

    exc = urllib.error.HTTPError("u", 404, "Not Found", {}, None)
    exc.read = lambda: b"<!DOCTYPE html><html dir='rtl'><head><meta charset='UTF-8'>"
    assert describe_error(exc) == "HTTP 404 Not Found"


# ---- הרשאות ומסך ----


SUPERADMIN_PHONE = "0506660001"


@pytest.fixture
def superadmin_client(app, client):
    app.config["SUPERADMIN_EMAILS"] = frozenset({SUPERADMIN})
    with app.app_context():
        organization = Organization.query.filter_by(slug="fixture-org").first()
        user = User(phone=SUPERADMIN_PHONE, email=SUPERADMIN, role="owner",
                    organization=organization)
        db.session.add(user)
        db.session.commit()
    client.post("/logout")
    client.post("/login", data={"phone": SUPERADMIN_PHONE})
    return client


ADMIN_POSTS = (
    "/admin/fleet-stats/start",
    "/admin/fleet-stats/step",
    "/admin/fleet-stats/cancel",
)


def test_the_unidentified_are_sent_to_login(visitor):
    response = visitor.get("/admin/fleet-stats")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_manager_without_superadmin_is_forbidden(auth_client):
    assert auth_client.get("/admin/fleet-stats").status_code == 403
    for path in ADMIN_POSTS:
        assert auth_client.post(path).status_code == 403


def test_superadmin_sees_the_screen(superadmin_client):
    response = superadmin_client.get("/admin/fleet-stats")
    assert response.status_code == 200
    assert "ספירת הרכבים הפעילים בישראל" in response.get_data(as_text=True)


def test_step_endpoint_counts_and_publishes(app, superadmin_client, monkeypatch):
    monkeypatch.setattr(fleet_import, "sql_page",
                        lambda offset, limit, min_count: pager(
                            [[sql_record("COROLLA", 90000)]])(offset))

    assert superadmin_client.post("/admin/fleet-stats/start").status_code == 200
    payload = superadmin_client.post("/admin/fleet-stats/step").get_json()
    assert payload["job"]["status"] == "done"
    assert payload["job"]["models"] == 1
    assert payload["snapshot"]["vehicles"] == 90000


def test_step_without_an_open_job_is_a_conflict(superadmin_client):
    assert superadmin_client.post("/admin/fleet-stats/step").status_code == 409


def test_status_endpoint_survives_a_reload(superadmin_client):
    superadmin_client.post("/admin/fleet-stats/start")
    payload = superadmin_client.get("/admin/fleet-stats/status").get_json()
    assert payload["job"]["is_running"] is True


def test_read_only_blocks_the_count(app, superadmin_client):
    app.config["READ_ONLY"] = True
    try:
        for path in ADMIN_POSTS:
            response = superadmin_client.post(
                path, headers={"X-Requested-With": "XMLHttpRequest"}
            )
            assert response.status_code == 403
            assert response.get_json()["read_only"] is True
    finally:
        app.config["READ_ONLY"] = False


def test_empty_stats_screen_points_a_superadmin_to_the_count(superadmin_client):
    html = superadmin_client.get("/stats").get_data(as_text=True)
    assert "/admin/fleet-stats" in html
