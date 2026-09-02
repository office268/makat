"""פירוט הכישלון: לא רק *מה קרה*, אלא *איפה זה נעצר*.

היומן הוא זרם - שליפה שנכשלת מייצרת ארבעים שורות, ומי שמסתכל צריך
להסיק מהן איזה שלב הרג אותה. השלבים אומרים את זה ישירות, וזה מה
שעולה למסך.
"""
import json

import pytest

from app import live_lookup, parts_discovery
from app.catalog_sources import Candidate, base, epc_vin, trace
from app.catalog_sources.base import Continuation
from app.models import db

from test_catalog_real_sources import FakeClient

VEHICLE = {"make": "טויוטה יפן", "model": "COROLLA", "year": 2011,
           "vin": "JTNBV58E20J147563", "engine_code": "1ZR", "plate": "1234567"}


def _page(html="<html><body>" + "מילה " * 60 + "</body></html>"):
    def fetcher(url, timeout=None):
        base.describe_page(html, url, final_url=url, status=200)
        return html
    return fetcher


# --------------------------------------------------------------------------
# השלבים עצמם
# --------------------------------------------------------------------------

def test_stages_are_separate_from_the_log():
    trace.start()
    trace.note("שורת יומן")
    trace.stage("שלב", True, "פרט")
    assert trace.lines() == ["שורת יומן"]
    assert trace.stages() == [{"name": "שלב", "ok": True, "detail": "פרט",
                               "hint": ""}]


def test_the_verdict_is_the_first_failure():
    trace.start()
    trace.stage("א", True)
    trace.stage("ב", False, "נפל כאן", "תעשה משהו")
    trace.stage("ג", False, "וגם כאן")
    assert trace.verdict()["name"] == "ב"
    assert trace.verdict()["hint"] == "תעשה משהו"


def test_no_failure_means_no_verdict():
    trace.start()
    trace.stage("א", True)
    assert trace.verdict() is None


def test_a_new_run_clears_the_stages():
    trace.start()
    trace.stage("ישן", False)
    trace.start()
    assert trace.stages() == []


# --------------------------------------------------------------------------
# מה כל סוג כישלון אומר
# --------------------------------------------------------------------------

def test_a_javascript_shell_is_told_apart_from_an_irrelevant_page():
    """דף גדול בלי טקסט הוא שלד שנבנה ב-JavaScript, וזו תקלה אחרת
    לגמרי מ"הדף לא רלוונטי"."""
    trace.start()
    base.condense("<html><body><div></div></body></html>" + "<i></i>" * 500,
                  "https://a.com/")
    failed = trace.verdict()
    assert failed["name"] == "תוכן הדף"
    assert "JavaScript" in failed["hint"]


def test_a_real_page_passes_the_content_stage():
    trace.start()
    base.condense("<html><body>" + "מילה " * 60 + "</body></html>", "https://a.com/")
    assert trace.verdict() is None


@pytest.mark.parametrize("code,needle", [
    (403, "SCRAPERAPI_PREMIUM"),
    (404, "EPC_VIN_URL"),
    (429, "CATALOG_HOST_PAUSE"),
])
def test_each_http_failure_says_what_to_change(code, needle):
    assert needle in base._http_hint(code)


def test_running_out_of_hops_is_a_named_failure(monkeypatch):
    """המסע היה בדרך הנכונה ופשוט נקטע - וזה נראה עד היום כמו
    "לא נמצא"."""
    monkeypatch.setattr(epc_vin, "MAX_HOPS", 2)
    monkeypatch.setattr(epc_vin, "URL_TEMPLATE", "https://a.com/?q={vin}")
    trace.start()
    with pytest.raises(base.FetchError):
        epc_vin.EpcVinSource().lookup(
            VEHICLE, "fuel_pump", fetcher=_page(),
            # כתובות שונות בכל צעד, אחרת המסע נעצר על "אין המשך חדש"
            client=FakeClient(*[{"parts": [], "vehicle_confirmed": False,
                                 "next_url": f"https://a.com/deeper/{n}/"}
                                for n in range(4)]),
        )
    names = [row["name"] for row in trace.stages() if not row["ok"]]
    assert "עומק המסע" in names
    depth = next(r for r in trace.stages() if r["name"] == "עומק המסע")
    assert "EPC_MAX_HOPS" in depth["hint"]


def test_the_model_explanation_reaches_the_log(monkeypatch):
    """כשאין מק"טים, מה שהמודל *כן* אמר הוא הראיה היחידה."""
    monkeypatch.setattr(epc_vin, "MAX_HOPS", 1)
    monkeypatch.setattr(epc_vin, "URL_TEMPLATE", "https://a.com/?q={vin}")
    trace.start()
    with pytest.raises(base.FetchError):
        epc_vin.EpcVinSource().lookup(
            VEHICLE, "fuel_pump", fetcher=_page(),
            client=FakeClient({"parts": [], "next_url": "",
                               "vehicle_confirmed": False,
                               "note": "העמוד מציג רשימת קבוצות ולא חלקים"}),
        )
    assert "רשימת קבוצות ולא חלקים" in "\n".join(trace.lines())


def test_a_part_note_reaches_the_log(monkeypatch):
    """ההסבר של המודל לכל מק"ט הוא מה שמסביר פסילה מאוחרת יותר."""
    monkeypatch.setattr(epc_vin, "MAX_HOPS", 1)
    monkeypatch.setattr(epc_vin, "URL_TEMPLATE", "https://a.com/?q={vin}")
    trace.start()
    epc_vin.EpcVinSource().lookup(
        VEHICLE, "fuel_pump", fetcher=_page(),
        client=FakeClient({"parts": [{"oe_number": "23220-0T030",
                                      "note": "מופיע בתרשים מערכת הדלק"}],
                           "next_url": "", "vehicle_confirmed": True}),
    )
    assert "מופיע בתרשים מערכת הדלק" in "\n".join(trace.lines())


# --------------------------------------------------------------------------
# האימות - מק"ט שנמצא ונפסל
# --------------------------------------------------------------------------

def test_validation_records_every_verdict():
    trace.start()
    accepted, rejected = parts_discovery.validate(
        [{"part_number": "OK-1", "manufacturer": "TOYOTA", "confidence": "high"},
         {"part_number": "NO-1", "manufacturer": "TOYOTA", "confidence": "low"}],
        "טויוטה", "COROLLA", "oil_filter",
    )
    log = "\n".join(trace.lines())
    assert "✓ OK-1" in log and "✗ NO-1" in log
    assert len(accepted) == 1 and len(rejected) == 1
    stage = next(r for r in trace.stages() if r["name"] == 'אימות המק"טים')
    assert stage["ok"] is True
    assert "1 אושרו, 1 נפסלו" in stage["detail"]


def test_everything_rejected_is_a_failed_stage_with_a_hint():
    """מק"ט שנמצא ונפסל נראה על המסך בדיוק כמו מק"ט שלא נמצא."""
    trace.start()
    parts_discovery.validate(
        [{"part_number": "NO-1", "manufacturer": "TOYOTA", "confidence": "low"}],
        "טויוטה", "COROLLA", "oil_filter",
    )
    stage = trace.verdict()
    assert stage["name"] == 'אימות המק"טים'
    assert "confidence=low" in stage["hint"]


def test_no_candidates_at_all_is_not_a_validation_failure():
    """כשלא הגיע כלום, האימות לא רץ - והכשל שייך לשלב שלפניו."""
    trace.start()
    parts_discovery.validate([], "טויוטה", "COROLLA", "oil_filter")
    assert trace.stages() == []


# --------------------------------------------------------------------------
# העבודה והמסך
# --------------------------------------------------------------------------

def _job(app, stages=("epc",)):
    job = live_lookup.LookupJob(
        plate="1234567", vin_key=live_lookup.vin_key(VEHICLE),
        part_type="fuel_pump", vehicle=json.dumps(VEHICLE),
        stages=json.dumps(list(stages)),
        results=json.dumps({"results": [], "unverified": []}),
    )
    db.session.add(job)
    db.session.commit()
    return job


def test_the_diagnosis_reaches_the_screen(app):
    with app.app_context():
        job = _job(app)

        def runner(source, vehicle, part_type, data, **_):
            trace.stage("הבאת הדף", True, "HTTP 200")
            trace.stage("זיהוי הרכב בדף", False, "אף עמוד לא אישר",
                        "ייתכן שהקטלוג אינו מכסה את היצרן")
            return []

        live_lookup.run_step(job, runner=runner)
        payload = job.to_dict()
        names = [row["name"] for row in payload["diagnosis"]]
        assert "המקור" in names and "זיהוי הרכב בדף" in names
        assert payload["failed_stage"]["name"] == "זיהוי הרכב בדף"
        assert "אינו מכסה" in payload["failed_stage"]["hint"]


def test_a_source_that_blew_up_is_the_first_failed_stage(app):
    with app.app_context():
        job = _job(app)

        def runner(source, vehicle, part_type, data, **_):
            raise base.FetchError("האתר החזיר 403")

        live_lookup.run_step(job, runner=runner)
        payload = job.to_dict()
        assert payload["failed_stage"]["name"] == "המקור"
        assert "403" in payload["failed_stage"]["detail"]


def test_read_only_is_told_apart_from_not_found(app):
    """"נמצא ולא נשמר" ו"לא נמצא" הן שתי תשובות שונות לגמרי."""
    with app.app_context():
        app.config["READ_ONLY"] = True

        def runner(source, vehicle, part_type, data, **_):
            return [Candidate(part_number="23220-0T030", manufacturer="TOYOTA",
                              tier="oem", confidence="high",
                              oe_number="23220-0T030")]

        job = _job(app)
        live_lookup.run_step(job, runner=runner)
        saving = next(r for r in job.to_dict()["diagnosis"]
                      if r["name"] == "שמירה בקטלוג")
        assert saving["ok"] is False
        assert "READ_ONLY" in saving["hint"]
        app.config["READ_ONLY"] = False


def test_a_successful_save_is_recorded_too(app):
    with app.app_context():
        def runner(source, vehicle, part_type, data, **_):
            return [Candidate(part_number="23220-0T031", manufacturer="TOYOTA",
                              tier="oem", confidence="high",
                              oe_number="23220-0T031")]

        job = _job(app)
        live_lookup.run_step(job, runner=runner)
        saving = next(r for r in job.to_dict()["diagnosis"]
                      if r["name"] == "שמירה בקטלוג")
        assert saving["ok"] is True


def test_a_paused_walk_keeps_its_diagnosis(app):
    """גם באמצע המסע כדאי לראות איפה עומדים."""
    with app.app_context():
        job = _job(app)

        def runner(source, vehicle, part_type, data, resume=None, **_):
            trace.stage("הבאת הדף", True, "HTTP 200")
            resume.url = "https://a.com/next/"
            return []

        live_lookup.run_step(job, runner=runner)
        assert [r["name"] for r in job.to_dict()["diagnosis"]] == \
            ["המקור", "הבאת הדף"]
