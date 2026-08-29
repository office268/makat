"""ייבוא קטלוג דגמי הרכב מתוך היישום, במנות."""
import json
import urllib.error
from contextlib import contextmanager

import pytest

from app.auth_models import Organization, User
from app.models import db
from app.vehicle_catalog import VehicleImportJob, VehicleModel, active_job
from app import vehicle_import

SUPERADMIN = "boss@t.test"


def record(make, model, year, code="A1"):
    return {
        "tozar": make, "kinuy_mishari": model, "degem_nm": code,
        "ramat_gimur": None, "shnat_yitzur": year, "nefah_manoa": 1600,
        "delek_nm": "בנזין", "merkav": "סדאן", "koah_sus": 120,
    }


def pager(pages, total=None):
    """fetch_page מזויף שמגיש רשימת עמודים לפי ה-offset המבוקש."""
    flat = [row for page in pages for row in page]
    total = len(flat) if total is None else total
    sizes = [len(page) for page in pages]

    def fetch(offset, page_size=None, timeout=None):
        position = 0
        for index, size in enumerate(sizes):
            if position == offset:
                return pages[index], total
            position += size
        return [], total

    return fetch


# ---- שכבת הרשת ----


def test_fetch_page_parses_ckan_response(monkeypatch):
    payload = {"result": {"records": [record("טויוטה", "COROLLA", 2015)], "total": 42}}

    @contextmanager
    def fake_urlopen(request, timeout=None):
        assert "offset=1000" in request.full_url

        class Response:
            def read(self):
                return json.dumps(payload).encode("utf-8")

        yield Response()

    monkeypatch.setattr(vehicle_import.urllib.request, "urlopen", fake_urlopen)
    records, total = vehicle_import.fetch_page(1000)
    assert total == 42
    assert records[0]["kinuy_mishari"] == "COROLLA"


# ---- מנות ----


def test_chunk_advances_offset_and_finishes(app):
    pages = [
        [record("טויוטה", "COROLLA", 2015), record("טויוטה", "COROLLA", 2016)],
        [record("מאזדה", "3", 2019)],
    ]
    with app.app_context():
        job = vehicle_import.start_job()
        vehicle_import.run_chunk(job, pages=1, fetch=pager(pages))
        assert job.offset == 2
        assert job.total == 3
        assert job.status == VehicleImportJob.RUNNING
        assert job.created == 1  # שתי שנות ייצור מתכווצות לדגם אחד

        vehicle_import.run_chunk(job, pages=1, fetch=pager(pages))
        assert job.status == VehicleImportJob.DONE
        assert job.offset == 3
        assert VehicleModel.query.count() == 2


def test_chunk_collapses_across_page_boundary(app):
    """אותו דגם משני צדי גבול עמוד נשמר פעם אחת עם טווח שנים מלא."""
    pages = [
        [record("טויוטה", "COROLLA", 2013)],
        [record("טויוטה", "COROLLA", 2018)],
    ]
    with app.app_context():
        job = vehicle_import.start_job()
        vehicle_import.run_chunk(job, pages=2, fetch=pager(pages))
        assert job.status == VehicleImportJob.DONE
        assert VehicleModel.query.count() == 1
        model = VehicleModel.query.one()
        assert (model.year_from, model.year_to) == (2013, 2018)


def test_job_resumes_from_saved_offset(app):
    """המנה הבאה יכולה לרוץ אצל worker אחר - המצב נקרא מחדש מה-DB."""
    pages = [[record("טויוטה", "COROLLA", 2015)], [record("מאזדה", "3", 2019)]]
    with app.app_context():
        job_id = vehicle_import.start_job().id
        vehicle_import.run_chunk(db.session.get(VehicleImportJob, job_id),
                                 pages=1, fetch=pager(pages))
        db.session.expunge_all()

        resumed = db.session.get(VehicleImportJob, job_id)
        assert resumed.offset == 1
        vehicle_import.run_chunk(resumed, pages=1, fetch=pager(pages))
        assert resumed.status == VehicleImportJob.DONE
        assert VehicleModel.query.count() == 2  # בלי כפילות של העמוד הראשון


def test_network_error_keeps_offset_and_job_open(app):
    pages = [[record("טויוטה", "COROLLA", 2015)], [record("מאזדה", "3", 2019)]]
    good = pager(pages)
    calls = {"n": 0}

    def flaky(offset, page_size=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.URLError("connection refused")
        return good(offset, page_size, timeout)

    with app.app_context():
        job = vehicle_import.start_job()
        vehicle_import.run_chunk(job, pages=1, fetch=flaky)
        assert job.offset == 0
        assert job.status == VehicleImportJob.RUNNING
        assert "connection refused" in job.error
        assert VehicleModel.query.count() == 0

        # ההמשך מצליח ומנקה את השגיאה
        vehicle_import.run_chunk(job, pages=2, fetch=good)
        assert job.status == VehicleImportJob.DONE
        assert job.error is None
        assert VehicleModel.query.count() == 2


def test_start_job_returns_the_open_one(app):
    with app.app_context():
        first = vehicle_import.start_job()
        assert vehicle_import.start_job().id == first.id
        vehicle_import.cancel_job(first)
        assert first.status == VehicleImportJob.CANCELLED
        assert active_job() is None
        assert vehicle_import.start_job().id != first.id


def test_cancelled_job_does_not_run(app):
    with app.app_context():
        job = vehicle_import.start_job()
        vehicle_import.cancel_job(job)
        vehicle_import.run_chunk(job, pages=1, fetch=pager([[record("א", "ב", 2020)]]))
        assert VehicleModel.query.count() == 0


# ---- הרשאות ומסך ----


@pytest.fixture
def superadmin_client(app, client):
    app.config["SUPERADMIN_EMAILS"] = frozenset({SUPERADMIN})
    with app.app_context():
        organization = Organization.query.filter_by(slug="fixture-org").first()
        user = User(email=SUPERADMIN, role="owner", organization=organization)
        user.set_password("password123")
        db.session.add(user)
        db.session.commit()
    client.post("/login", data={"email": SUPERADMIN, "password": "password123"})
    return client


ADMIN_POSTS = (
    "/admin/vehicle-import/start",
    "/admin/vehicle-import/step",
    "/admin/vehicle-import/cancel",
)


def test_anonymous_is_sent_to_login(client):
    response = client.get("/admin/vehicle-import")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_manager_without_superadmin_is_forbidden(auth_client):
    assert auth_client.get("/admin/vehicle-import").status_code == 403
    for path in ADMIN_POSTS:
        assert auth_client.post(path).status_code == 403


def test_superadmin_sees_the_screen(superadmin_client):
    response = superadmin_client.get("/admin/vehicle-import")
    assert response.status_code == 200
    assert "ייבוא קטלוג דגמי רכב" in response.get_data(as_text=True)


def test_step_endpoint_reports_progress(app, superadmin_client, monkeypatch):
    pages = [[record("טויוטה", "COROLLA", 2015)]]
    monkeypatch.setattr(vehicle_import, "fetch_page", pager(pages))

    assert superadmin_client.post("/admin/vehicle-import/start").status_code == 200
    payload = superadmin_client.post("/admin/vehicle-import/step").get_json()
    assert payload["job"]["status"] == "done"
    assert payload["job"]["created"] == 1
    assert payload["models_in_catalog"] == 1


def test_step_without_an_open_job_is_a_conflict(superadmin_client):
    assert superadmin_client.post("/admin/vehicle-import/step").status_code == 409


def test_status_endpoint_survives_a_reload(superadmin_client):
    superadmin_client.post("/admin/vehicle-import/start")
    payload = superadmin_client.get("/admin/vehicle-import/status").get_json()
    assert payload["job"]["is_running"] is True


def test_read_only_blocks_the_import(app, superadmin_client):
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


def test_is_superadmin_follows_the_config(app, superadmin_client):
    with app.app_context():
        user = User.query.filter_by(email=SUPERADMIN).first()
        assert user.is_superadmin is True
        app.config["SUPERADMIN_EMAILS"] = frozenset()
        assert user.is_superadmin is False
    assert superadmin_client.get("/admin/vehicle-import").status_code == 403
