"""שער אישור בין מקור למקור בשליפה החיה.

כל מקור הוא בקשת רשת וקריאת מודל, ונספר במכסה היומית של הארגון. מי
שכבר קיבל את המק"ט המקורי לשלדה שלו לא בהכרח רוצה שנמשיך לחפש לו
חלופות, ולכן ההמשכה היא בחירה.
"""
import json

import pytest

from app import live_lookup
from app.auth_models import Organization, User
from app.catalog_sources import Candidate
from app.models import Part, db

VEHICLE = {"make": "טויוטה", "model": "COROLLA", "year": 2015,
           "vin": "JTDBR32E560012345", "engine_code": "2ZR-FAE", "plate": "1234567"}

OE = "04152-YZZA1"


def _job(**kwargs):
    job = live_lookup.LookupJob(
        plate="1234567", vin_key=live_lookup.vin_key(VEHICLE),
        part_type="oil_filter", vehicle=json.dumps(VEHICLE),
        stages=json.dumps(["laximo", "tecdoc"]),
        results=json.dumps({"results": [], "unverified": []}),
        **kwargs,
    )
    db.session.add(job)
    db.session.commit()
    return job


def _sources(seen=None):
    """שני מקורות מדומים. השני רושם מה קיבל, כדי לבדוק את השרשרת."""
    def runner(source, vehicle, part_type, data, **_):
        if source.tier == "oem":
            return [Candidate(part_number=OE, manufacturer="TOYOTA",
                              tier="oem", confidence="high", oe_number=OE)]
        if seen is not None:
            seen["oem"] = live_lookup.known_oem_numbers(
                vehicle, part_type, data.get("results") or []
            )
        return [Candidate(part_number="OC90", manufacturer="MAHLE",
                          tier="aftermarket", confidence="high", oe_number=OE)]
    return runner


def test_the_run_pauses_after_each_source_that_has_a_next_one(app):
    with app.app_context():
        job = _job()
        live_lookup.run_step(job, runner=_sources())
        assert job.awaiting_approval is True
        assert job.is_running
        assert job.cursor == 1 and job.total == 2
        # מה שהמסך מציג: מה שכבר נמצא, ומי המקור הבא
        payload = job.to_dict()
        assert payload["awaiting_approval"] is True
        assert [r["part_number"] for r in payload["results"]] == [OE]
        assert payload["done_stage_label"]
        assert payload["stage_label"]
        assert payload["done_stage_label"] != payload["stage_label"]


def test_the_last_source_does_not_ask(app):
    """אין למה להמשיך, ולכן אין שאלה - הריצה נסגרת לבד."""
    with app.app_context():
        job = _job()
        live_lookup.run_step(job, runner=_sources())
        live_lookup.run_step(job, runner=_sources())
        assert job.awaiting_approval is False
        assert job.status == live_lookup.LookupJob.DONE


def test_approving_keeps_the_oem_chain_intact(app):
    """זו השאלה האמיתית: TecDoc מחפש לפי המספר ש-Laximo הוציא.

    ההפסקה יכולה להיות ארוכה - התוצאה יושבת בעמודה ב-DB ולא בזיכרון -
    ולכן המקור השני מקבל בדיוק את מה שהיה מקבל בריצה רצופה.
    """
    seen = {}
    with app.app_context():
        job = _job()
        live_lookup.run_step(job, runner=_sources(seen))
        assert job.awaiting_approval, "עצרנו"
        live_lookup.run_step(job, runner=_sources(seen))   # המשתמש אישר
        assert seen["oem"] == [OE]
        assert [r["part_number"] for r in job.to_dict()["results"]] == [OE, "OC90"]


def test_stopping_ends_the_job_without_caching_a_partial_answer(app):
    """תשובה חלקית בבחירה של מי ששאל אינה תשובה למי שישאל אחריו."""
    with app.app_context():
        job = _job()
        live_lookup.run_step(job, runner=_sources())
        live_lookup.stop_job(job)

        assert job.status == live_lookup.LookupJob.STOPPED
        assert job.awaiting_approval is False
        assert job.finished_at is not None
        assert live_lookup.cached(VEHICLE, "oil_filter") is None


def test_a_completed_run_still_caches(app):
    """בקרה: השער לא שינה את מה שקורה לריצה שהושלמה."""
    with app.app_context():
        job = _job()
        live_lookup.run_step(job, runner=_sources())
        live_lookup.run_step(job, runner=_sources())
        hit = live_lookup.cached(VEHICLE, "oil_filter")
        assert hit is not None
        assert [r["part_number"] for r in hit["results"]] == [OE, "OC90"]


def test_what_the_first_source_found_is_in_the_catalog_even_after_stopping(app):
    """העצירה אינה מאבדת את מה שכבר נמצא - הכתיבה לקטלוג היא לכל מקור."""
    with app.app_context():
        job = _job()
        live_lookup.run_step(job, runner=_sources())
        live_lookup.stop_job(job)
        assert Part.query.filter_by(part_number=OE).one()


def test_a_stopped_job_cannot_be_stepped_further(client, app):
    with app.app_context():
        job = _job()
        live_lookup.run_step(job, runner=_sources())
        live_lookup.stop_job(job)
        job_id = job.id
    response = client.post("/lookup/step", data={"job": job_id})
    assert response.status_code == 409


def test_stopping_someone_elses_job_is_refused(app, org_id):
    """אותו בודק בעלות כמו step ו-cancel."""
    with app.app_context():
        owner = User.query.filter_by(phone="0500000001").one()
        other_org = Organization(name="מוסך אחר", slug="other-gate")
        db.session.add(other_org)
        db.session.flush()
        intruder = User(phone="0500000098", email="gate@t.test", role="owner",
                        organization=other_org)
        db.session.add(intruder)
        job = _job(organization_id=org_id, started_by_id=owner.id)
        db.session.commit()
        job_id, intruder_id = job.id, intruder.id

    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(intruder_id)
        session["_fresh"] = True
    assert client.post("/lookup/stop", data={"job": job_id}).status_code == 403
    with app.app_context():
        assert db.session.get(live_lookup.LookupJob, job_id).is_running


def test_stopping_through_the_route(client, app):
    with app.app_context():
        owner = User.query.filter_by(phone="0500000001").one()
        job = _job(started_by_id=owner.id)
        live_lookup.run_step(job, runner=_sources())
        job_id = job.id

    payload = client.post("/lookup/stop", data={"job": job_id}).get_json()
    assert payload["job"]["status"] == "stopped"
    assert payload["job"]["awaiting_approval"] is False
    assert [r["part_number"] for r in payload["job"]["results"]] == [OE]


@pytest.mark.parametrize("stages", [["laximo"], ["laximo", "tecdoc", "epc"]])
def test_the_gate_follows_the_configured_source_list(app, stages):
    """מקור יחיד לא שואל כלום; שלושה שואלים פעמיים."""
    with app.app_context():
        job = _job()
        job.stages = json.dumps(stages)
        db.session.commit()

        asked = 0
        for _ in stages:
            live_lookup.run_step(job, runner=_sources())
            if job.awaiting_approval:
                asked += 1
        assert asked == len(stages) - 1


# --------------------------------------------------------------------------
# מקור שלא מצא כלום אינו "נמצא"
# --------------------------------------------------------------------------

def _nothing(source, vehicle, part_type, data, **_):
    """המקור ענה כראוי, ופשוט אין לו את החלק לרכב הזה."""
    return []


def _broken(source, vehicle, part_type, data, **_):
    from app.catalog_sources.base import FetchError

    raise FetchError("פסק זמן בטעינת laximo.ru")


def test_an_empty_stage_reports_nothing_found_not_a_success(app):
    """המסך הודיע "נמצא ב-Laximo" והציג מתחתיו כלום.

    שלב שהסתיים אינו שלב שמצא, והכיתוב נכתב בלי לבדוק. התשובה
    שהמשתמש קיבל הייתה הצלחה ריקה - גרוע מ"לא נמצא", כי אין ממנה
    מה ללמוד ואין מה לעשות אחריה.
    """
    with app.app_context():
        job = _job()
        live_lookup.run_step(job, runner=_nothing)
        payload = job.to_dict()

        assert payload["awaiting_approval"] is True, "עדיין נעצרים ושואלים"
        assert payload["results"] == []
        assert payload["unverified"] == []
        # מה שהמסך צריך כדי לומר את האמת: שם המקור שסיים, ואין תוצאות
        assert payload["done_stage_label"]
        assert payload["stage_label"], "ויש עוד מקור לנסות בו"


def test_a_failed_stage_carries_its_reason_to_the_screen(app):
    """תקלת רשת ו"אין לרכב הזה" הן שתי תשובות שונות לגמרי.

    רק אחת מהן אומרת "נסה שוב", ולכן הסיבה חייבת להגיע למסך.
    """
    with app.app_context():
        job = _job()
        live_lookup.run_step(job, runner=_broken)
        payload = job.to_dict()

        assert payload["error"]
        assert "פסק זמן" in payload["error"]
        # ‏job.error כבר נושא את שם המקור, ולכן המסך מציג אותו כמו שהוא
        assert payload["error"].startswith(payload["done_stage_label"])
        assert payload["results"] == []


def test_a_stage_that_found_something_still_says_so(app):
    """בקרה: השינוי לא הפך הצלחה אמיתית לדיווח על כישלון."""
    with app.app_context():
        job = _job()
        live_lookup.run_step(job, runner=_sources())
        payload = job.to_dict()
        assert [row["part_number"] for row in payload["results"]] == [OE]
        assert payload["error"] is None
