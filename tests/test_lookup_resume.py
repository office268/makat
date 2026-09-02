"""ניווט בקטלוג שלא נכנס בבקשה אחת, וממשיך בבאה.

מה שנמדד בשטח: קטלוג יצרן הוא עץ - עמוד רכב, קבוצה, תרשים, ורק שם
המק"טים. כל הבאה דרך ScraperAPI לקחה 30-45 שניות, ו-gunicorn הורג
בקשה אחרי WEB_TIMEOUT. שני צעדים כבר מילאו את התקציב, והמק"טים
נשארו צעד אחד משם - ריצה שנגמרה ב'לא נמצאו תוצאות מאומתות' אחרי
שהרכב *כן* זוהה נכון.
"""
import json

import pytest

from app import live_lookup
from app.catalog_sources import Candidate, base, epc_vin, trace
from app.catalog_sources.base import Continuation
from app.models import db

from test_catalog_real_sources import FakeClient

VEHICLE = {"make": "טויוטה יפן", "model": "COROLLA", "year": 2011,
           "vin": "JTNBV58E20J147563", "engine_code": "1ZR", "plate": "1234567"}

VIN_PAGE = {"parts": [], "vehicle_confirmed": True,
            "next_url": "https://partsouq.com/en/catalog/toyota/category/1/"}
GROUP_PAGE = {"parts": [], "vehicle_confirmed": True,
              "next_url": "https://partsouq.com/en/catalog/toyota/group/fuel/"}
DIAGRAM_PAGE = {"parts": [{"oe_number": "23220-0T030", "name": "Fuel pump",
                           "confidence": "high"}],
                "vehicle_confirmed": True, "next_url": ""}


def _fetcher(seen):
    def fetch(url, timeout=None):
        seen.append(url)
        return "<html><body>x</body></html>"
    return fetch


# --------------------------------------------------------------------------
# צמצום הדף
# --------------------------------------------------------------------------

def test_the_currency_picker_does_not_reach_the_model():
    """מה שהיה בפועל: פותח הטקסט שנשלח למודל היה רשימת המטבעות
    המלאה של PartSouq, והחלקים נדחקו אל מעבר לחיתוך."""
    html = """<html><body>
      <nav>Home Catalogs Brands Shop</nav>
      <select name="currency">
        <option>USD United States dollar</option>
        <option>EUR European euro</option>
        <option>SAR Saudi riyal</option>
      </select>
      <h1>Toyota COROLLA 2011</h1>
      <a href="/diagram/fuel">FUEL PUMP 2320</a>
      <footer>About Us Contact News FAQ Policies</footer>
    </body></html>"""
    text = base.condense(html, "https://partsouq.com/")
    assert "Toyota COROLLA 2011" in text
    assert "FUEL PUMP 2320" in text
    for chrome in ("United States dollar", "Saudi riyal", "Home Catalogs",
                   "Policies"):
        assert chrome not in text


# --------------------------------------------------------------------------
# עצירה על תקציב הזמן
# --------------------------------------------------------------------------

def test_the_hop_loop_stops_on_the_deadline_and_says_where_to_resume(monkeypatch):
    monkeypatch.setattr(epc_vin, "MAX_HOPS", 4)
    monkeypatch.setattr(epc_vin, "URL_TEMPLATE", "https://partsouq.com/s?q={vin}")
    resume = Continuation()
    seen = []
    trace.start()
    found = epc_vin.EpcVinSource().lookup(
        VEHICLE, "fuel_pump", fetcher=_fetcher(seen), client=FakeClient(VIN_PAGE),
        resume=resume,
        deadline=base.time.monotonic() - 1,   # התקציב כבר נגמר
    )
    assert found == []
    assert resume.url == VIN_PAGE["next_url"]
    assert resume.hop == 1
    assert len(seen) == 1, "צעד אחד בלבד היה אמור להישלח"
    assert "תקציב הבקשה נגמר" in "\n".join(trace.lines())


def test_a_deadline_that_holds_lets_the_walk_finish(monkeypatch):
    monkeypatch.setattr(epc_vin, "MAX_HOPS", 4)
    monkeypatch.setattr(epc_vin, "URL_TEMPLATE", "https://partsouq.com/s?q={vin}")
    resume = Continuation()
    found = epc_vin.EpcVinSource().lookup(
        VEHICLE, "fuel_pump", fetcher=_fetcher([]),
        client=FakeClient(VIN_PAGE, GROUP_PAGE, DIAGRAM_PAGE),
        resume=resume, deadline=base.time.monotonic() + 300,
    )
    assert [c.part_number for c in found] == ["23220-0T030"]
    assert resume.url == "", "מסע שהסתיים לא משאיר המשך"


def test_resuming_starts_from_the_saved_url_not_from_the_beginning(monkeypatch):
    """הצעדים שכבר שולמו לא משולמים שוב."""
    monkeypatch.setattr(epc_vin, "MAX_HOPS", 4)
    seen = []
    resume = Continuation(url=GROUP_PAGE["next_url"], hop=2)
    found = epc_vin.EpcVinSource().lookup(
        VEHICLE, "fuel_pump", fetcher=_fetcher(seen),
        client=FakeClient(DIAGRAM_PAGE), resume=resume,
        deadline=base.time.monotonic() + 300,
    )
    assert seen == [GROUP_PAGE["next_url"]]
    assert [c.part_number for c in found] == ["23220-0T030"]
    assert resume.url == ""


# --------------------------------------------------------------------------
# העבודה
# --------------------------------------------------------------------------

def _job(stages=("epc", "aftermarket")):
    job = live_lookup.LookupJob(
        plate="1234567", vin_key=live_lookup.vin_key(VEHICLE),
        part_type="fuel_pump", vehicle=json.dumps(VEHICLE),
        stages=json.dumps(list(stages)),
        results=json.dumps({"results": [], "unverified": []}),
    )
    db.session.add(job)
    db.session.commit()
    return job


def test_a_paused_source_keeps_the_cursor_where_it_is(app):
    """‏cursor הוא *המקור*, לא הצעד. מקור שנעצר באמצע עדיין המקור
    הנוכחי, ולקדם אותו היה מדלג עליו בלי לסיים אותו."""
    with app.app_context():
        job = _job()

        def runner(source, vehicle, part_type, data, resume=None):
            resume.url, resume.hop = "https://partsouq.com/next/", 2
            return []

        live_lookup.run_step(job, runner=runner)
        assert job.cursor == 0, "המקור טרם סיים"
        assert job.resume_url == "https://partsouq.com/next/"
        assert job.resume_hop == 2
        assert job.is_running
        payload = job.to_dict()
        assert payload["resuming"] is True and payload["hop"] == 2


def test_the_next_request_hands_the_continuation_back(app):
    with app.app_context():
        job = _job()
        job.resume_url, job.resume_hop = "https://partsouq.com/next/", 2
        db.session.commit()
        seen = {}

        def runner(source, vehicle, part_type, data, resume=None):
            seen["url"], seen["hop"] = resume.url, resume.hop
            resume.clear()
            return [Candidate(part_number="23220-0T030", manufacturer="TOYOTA",
                              tier="oem", confidence="high",
                              oe_number="23220-0T030")]

        live_lookup.run_step(job, runner=runner)
        assert seen == {"url": "https://partsouq.com/next/", "hop": 2}
        # המקור סיים - עכשיו מתקדמים למקור הבא, וההמשך נמחק
        assert job.cursor == 1
        assert job.resume_url == ""
        payload = job.to_dict()
        shown = (payload["results"] or []) + (payload["unverified"] or [])
        assert [r["part_number"] for r in shown] == ["23220-0T030"]


def test_a_paused_source_writes_nothing_to_the_catalog_yet(app):
    """תוצאה חלקית של מסע שלא הסתיים אינה תוצאה."""
    with app.app_context():
        job = _job()

        def runner(source, vehicle, part_type, data, resume=None):
            resume.url = "https://partsouq.com/next/"
            return []

        live_lookup.run_step(job, runner=runner)
        payload = job.to_dict()
        assert payload["results"] == [] and payload["unverified"] == []
        assert job.saved == 0
        assert payload["error"] is None


def test_a_failure_clears_the_continuation(app):
    """אחרת הבקשה הבאה הייתה מנסה להמשיך מסע שכבר מת."""
    with app.app_context():
        job = _job()
        job.resume_url, job.resume_hop = "https://partsouq.com/next/", 2
        db.session.commit()

        def runner(source, vehicle, part_type, data, resume=None):
            raise base.FetchError("האתר החזיר 404")

        live_lookup.run_step(job, runner=runner)
        assert job.resume_url == "" and job.resume_hop == 0
        assert job.cursor == 1


def test_the_paused_step_still_records_its_log(app):
    with app.app_context():
        job = _job()

        def runner(source, vehicle, part_type, data, resume=None):
            trace.note("→ ScraperAPI: https://partsouq.com/en/catalog/…")
            resume.url = "https://partsouq.com/next/"
            return []

        live_lookup.run_step(job, runner=runner)
        assert "partsouq.com/en/catalog" in "\n".join(job.to_dict()["log"])


def test_only_a_source_that_asked_for_it_gets_the_continuation(app):
    """מקור שמביא עמוד אחד אינו יודע לקבל resume/deadline, ולשלוח לו
    אותם היה מפיל אותו ב-TypeError שנראה כמו תקלת רשת."""
    with app.app_context():
        seen = {}

        class Plain:
            key, name, tier, needs_vin = "plain", "פשוט", "oem", False
            supports_resume = False

            def lookup(self, vehicle, part_type, oem_numbers=(), **kwargs):
                seen["kwargs"] = sorted(kwargs)
                return []

        live_lookup._run_source(Plain(), VEHICLE, "fuel_pump", {},
                                resume=Continuation())
        assert seen["kwargs"] == []

        class Walker(Plain):
            supports_resume = True

        live_lookup._run_source(Walker(), VEHICLE, "fuel_pump", {},
                                resume=Continuation())
        assert seen["kwargs"] == ["deadline", "resume"]


def test_the_step_budget_leaves_room_under_gunicorn():
    """התקציב נגזר מ-WEB_TIMEOUT ולא נבחר ביד, כדי שהשניים לא יסתרו."""
    assert live_lookup.STEP_BUDGET > 0
    assert live_lookup.STEP_BUDGET < float(live_lookup.os.environ.get("WEB_TIMEOUT", 60))
