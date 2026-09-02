"""יומן החקירה: שלוש תשובות שנראות זהות על המסך, ואיך מבדילים ביניהן.

'לא החזיר מק"ט' יכול להיות אתר שהפנה אותנו לדף הבית, דף תוצאות אמיתי
שאין בו החלק, או שלד ריק שנבנה ב-JavaScript. הבדיקות כאן מוודאות
שהיומן אומר איזה מהם - וגם שהוא לא גורר שורות משלב לשלב, ולא מפיל
שליפה שהצליחה רק כי כותרת HTTP חסרה.
"""
import json

import pytest

from app import live_lookup
from app.catalog_sources import Candidate, base, epc_vin, trace
from app.models import db

from test_catalog_real_sources import FakeClient

VEHICLE = {"make": "פיג'ו צרפת", "model": "208", "year": 2020,
           "vin": "VF3M45GFRLS125956", "engine_code": "HN05", "plate": "1234567"}


# --------------------------------------------------------------------------
# היומן עצמו
# --------------------------------------------------------------------------

def test_a_note_without_an_open_log_is_dropped_and_does_not_explode():
    trace.clear()
    trace.note("לאן זה הולך")
    assert trace.lines() == []


def test_each_start_opens_a_clean_log():
    trace.start()
    trace.note("שלב א")
    trace.start()
    trace.note("שלב ב")
    assert trace.lines() == ["שלב ב"]


def test_the_log_has_a_ceiling_and_says_when_it_was_cut(monkeypatch):
    monkeypatch.setattr(trace, "MAX_LINES", 3)
    trace.start()
    for index in range(10):
        trace.note(f"שורה {index}")
    lines = trace.lines()
    assert len(lines) == 4
    assert lines[-1] == "… היומן נקטע"


def test_the_returned_lines_are_a_copy():
    trace.start()
    trace.note("שורה")
    lines = trace.lines()
    lines.append("זיוף")
    assert trace.lines() == ["שורה"]


def test_the_page_title_is_what_separates_a_result_page_from_a_landing_page():
    assert trace.page_title("<html><head><title>7zap - Home</title></head>") \
        == "7zap - Home"
    assert trace.page_title("<html><body>אין כותרת</body></html>") == ""


def test_the_preview_is_one_line_and_bounded(monkeypatch):
    monkeypatch.setattr(trace, "PREVIEW_CHARS", 10)
    trace.start()
    trace.preview("שורה\nראשונה   ושנייה ועוד המון טקסט")
    line = trace.lines()[0]
    assert "\n" not in line
    assert line.endswith("…")


# --------------------------------------------------------------------------
# ההבאה
# --------------------------------------------------------------------------

class FakeHeaders(dict):
    def get_content_charset(self):
        return "utf-8"


class FakeResponse:
    def __init__(self, body, url="", status=200, content_type="text/html"):
        self._body = body.encode("utf-8")
        self.url = url
        self.status = status
        self.headers = FakeHeaders({"Content-Type": content_type})

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _no_robots(monkeypatch):
    monkeypatch.setattr(base, "allowed_by_robots", lambda url, agent=None: True)
    monkeypatch.setattr(base, "_breathe", lambda url: None)


def test_a_redirect_to_another_page_is_written_down(monkeypatch):
    """‏200 ודף תקין אינם 'הגענו לאן שביקשנו'. בלי השורה הזו, הפניה
    לדף הבית נראית בדיוק כמו חיפוש שלא מצא כלום."""
    _no_robots(monkeypatch)
    monkeypatch.setattr(
        base.urllib.request, "urlopen",
        lambda *a, **k: FakeResponse(
            "<html><head><title>7zap - Home</title></head><body>x</body></html>",
            url="https://7zap.com/en/",
        ),
    )
    trace.start()
    base.fetch("https://7zap.com/en/search/?q=VF3M45GFRLS125956")
    log = "\n".join(trace.lines())
    assert "הופנינו אל: https://7zap.com/en/" in log
    assert "כותרת: 7zap - Home" in log
    assert "HTTP 200" in log


def test_a_page_that_came_from_where_we_asked_has_no_redirect_line(monkeypatch):
    _no_robots(monkeypatch)
    url = "https://7zap.com/en/search/?q=X"
    monkeypatch.setattr(base.urllib.request, "urlopen",
                        lambda *a, **k: FakeResponse("<html></html>", url=url))
    trace.start()
    base.fetch(url)
    assert "הופנינו אל" not in "\n".join(trace.lines())


def test_a_blocked_url_says_so_in_the_log(monkeypatch):
    monkeypatch.setattr(base, "allowed_by_robots", lambda url, agent=None: False)
    trace.start()
    with pytest.raises(base.FetchError):
        base.fetch("https://example.com/x")
    assert "נחסם ב-robots.txt" in "\n".join(trace.lines())


def test_a_missing_content_type_header_does_not_break_a_successful_fetch(monkeypatch):
    """היומן הוא כלי עזר. שרת בלי הכותרת הזו לא יפיל שליפה שהצליחה."""
    _no_robots(monkeypatch)

    class Bare:
        headers = object()
        url = ""
        status = 200

        def read(self):
            return b"<html>ok</html>"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    # ‏get_content_charset חסר גם הוא - הקוד נופל אחורה ל-utf-8
    Bare.headers = FakeHeaders()
    monkeypatch.setattr(base.urllib.request, "urlopen", lambda *a, **k: Bare())
    trace.start()
    assert base.fetch("https://example.com/") == "<html>ok</html>"
    assert base.content_type(Bare()) == ""


def test_condensing_records_how_much_survived_and_what_the_model_will_see():
    trace.start()
    base.condense("<html><body><h1>Peugeot 208</h1><p>1525 QN</p></body></html>",
                  "https://7zap.com/x")
    log = "\n".join(trace.lines())
    assert "צמצום:" in log
    assert "Peugeot 208" in log


def test_condensing_says_when_the_text_was_cut(monkeypatch):
    trace.start()
    base.condense("<html><body>" + ("א" * 500) + "</body></html>", limit=50)
    assert "נחתך ל-50" in "\n".join(trace.lines())


# --------------------------------------------------------------------------
# המקור
# --------------------------------------------------------------------------

def test_the_epc_log_says_what_the_model_understood(monkeypatch):
    """הפרט המכריע: האם הדף אישר את הרכב, וכמה מק"טים יצאו ממנו."""
    monkeypatch.setattr(epc_vin, "MAX_HOPS", 1)
    client = FakeClient({"parts": [], "next_url": "", "vehicle_confirmed": False})
    trace.start()
    # דף שלא אישר את הרכב הוא "האתר אינו מכסה אותו", ולכן תקלה ולא
    # תשובה ריקה. היומן נכתב לפני כן, וזה מה שנבדק כאן.
    with pytest.raises(base.FetchError):
        epc_vin.EpcVinSource().lookup(
            VEHICLE, "fuel_pump",
            fetcher=lambda url, timeout=None: "<html><body>Home</body></html>",
            client=client,
        )
    log = "\n".join(trace.lines())
    assert VEHICLE["vin"] in log
    assert "הרכב אושר בדף: לא" in log
    assert 'תוצאת הפענוח: 0 מק"טים' in log
    assert "אין המשך לעקוב אחריו" in log


def test_the_epc_log_names_every_number_it_brought_back(monkeypatch):
    monkeypatch.setattr(epc_vin, "MAX_HOPS", 1)
    client = FakeClient({
        "parts": [{"oe_number": "1525 QN", "name": "Fuel pump",
                   "confidence": "high"}],
        "vehicle_confirmed": True,
    })
    trace.start()
    found = epc_vin.EpcVinSource().lookup(
        VEHICLE, "fuel_pump",
        fetcher=lambda url, timeout=None: "<html><body>1525 QN</body></html>",
        client=client,
    )
    log = "\n".join(trace.lines())
    assert [c.part_number for c in found] == ["1525 QN"]
    assert "הרכב אושר בדף: כן" in log
    assert "1525 QN" in log


def test_a_second_hop_is_visible_as_a_second_step(monkeypatch):
    monkeypatch.setattr(epc_vin, "MAX_HOPS", 2)
    client = FakeClient(
        {"parts": [], "next_url": "https://7zap.com/en/peugeot/208/", },
        {"parts": [{"oe_number": "1525 QN"}], "vehicle_confirmed": True},
    )
    trace.start()
    epc_vin.EpcVinSource().lookup(
        VEHICLE, "fuel_pump",
        fetcher=lambda url, timeout=None: f"<html><body>{url}</body></html>",
        client=client,
    )
    log = "\n".join(trace.lines())
    assert "— צעד 1/2 —" in log
    assert "— צעד 2/2 —" in log
    assert "המשך מוצע: https://7zap.com/en/peugeot/208/" in log


# --------------------------------------------------------------------------
# העבודה
# --------------------------------------------------------------------------

def _job(stages=("epc",)):
    job = live_lookup.LookupJob(
        plate="1234567", vin_key=live_lookup.vin_key(VEHICLE),
        part_type="fuel_pump", vehicle=json.dumps(VEHICLE),
        stages=json.dumps(list(stages)),
        results=json.dumps({"results": [], "unverified": []}),
    )
    db.session.add(job)
    db.session.commit()
    return job


def test_the_job_log_opens_with_what_is_actually_running(app, monkeypatch):
    """השאלה הראשונה בכל חקירה: האם משתנה הסביבה שהוגדר אכן נתפס."""
    source = epc_vin.EpcVinSource()
    monkeypatch.setattr(source, "available", lambda: True)
    monkeypatch.setattr(live_lookup, "usable_sources", lambda vehicle: [source])
    with app.app_context():
        job = live_lookup.start_job(VEHICLE, "fuel_pump")
        log = "\n".join(job.to_dict()["log"])
        assert "מקורות לפי הסדר:" in log
        assert "מסלול הבאה:" in log
        assert VEHICLE["vin"] in log


def test_a_step_writes_its_trace_into_the_job(app, monkeypatch):
    with app.app_context():
        job = _job()

        def runner(source, vehicle, part_type, data, **_):
            trace.note("שורה מתוך המקור")
            return [Candidate(part_number="1525QN", tier="oem",
                              confidence="high", oe_number="1525QN")]

        live_lookup.run_step(job, runner=runner)
        assert "שורה מתוך המקור" in "\n".join(job.to_dict()["log"])


def test_a_failing_step_keeps_the_trace_that_explains_where_it_died(app):
    """בכשל היומן הוא כל מה שיש: ההודעה אומרת מה קרה, היומן איפה."""
    with app.app_context():
        job = _job()

        def runner(source, vehicle, part_type, data, **_):
            trace.note("→ הבאה ישירה: https://7zap.com/en/search/?q=X")
            raise base.FetchError("האתר החזיר 404")

        live_lookup.run_step(job, runner=runner)
        log = "\n".join(job.to_dict()["log"])
        assert "https://7zap.com/en/search/?q=X" in log
        assert "האתר החזיר 404" in log


def test_one_step_does_not_inherit_the_lines_of_the_one_before_it(app):
    with app.app_context():
        job = _job(stages=("epc", "aftermarket"))
        marks = ["ראשון", "שני"]

        def runner(source, vehicle, part_type, data, **_):
            trace.note(marks[job.cursor])
            return []

        live_lookup.run_step(job, runner=runner)
        first = trace.lines()
        live_lookup.run_step(job, runner=runner)
        assert "ראשון" in first and "שני" not in first
        assert trace.lines() == ["שני"]
        # שתי השורות נצברו בעבודה עצמה, כל אחת פעם אחת
        log = "\n".join(job.to_dict()["log"])
        assert log.count("ראשון") == 1 and log.count("שני") == 1


def test_a_rejected_number_says_why_it_was_rejected(app):
    """מק"ט שנמצא ונפסל באימות ומק"ט שלא נמצא בכלל נראים זהים על
    המסך, והתיקון שלהם שונה לגמרי."""
    with app.app_context():
        job = _job()

        def runner(source, vehicle, part_type, data, **_):
            return [Candidate(part_number="", tier="oem", confidence="low")]

        live_lookup.run_step(job, runner=runner)
        log = "\n".join(job.to_dict()["log"])
        assert "לא אומתו" in log


def test_the_log_that_reaches_the_screen_is_bounded(app, monkeypatch):
    monkeypatch.setattr(live_lookup, "LOG_LINES", 5)
    with app.app_context():
        job = _job()

        def runner(source, vehicle, part_type, data, **_):
            for index in range(50):
                trace.note(f"שורה {index}")
            return []

        live_lookup.run_step(job, runner=runner)
        assert len(job.to_dict()["log"]) == 5


def test_the_stored_log_is_bounded_too(app, monkeypatch):
    monkeypatch.setattr(live_lookup, "LOG_CHARS", 200)
    with app.app_context():
        job = _job()

        def runner(source, vehicle, part_type, data, **_):
            for index in range(200):
                trace.note(f"שורה ארוכה מאוד מספר {index}")
            return []

        live_lookup.run_step(job, runner=runner)
        assert len(job.log) <= 200


# --------------------------------------------------------------------------
# היומן ב-stdout: אותן שורות, במקום שאפשר לקרוא ממנו מרחוק
# --------------------------------------------------------------------------

def test_the_trace_also_goes_to_stdout_so_it_can_be_read_from_the_server_log(
        app, capsys):
    """היומן בבסיס הנתונים עונה למי שמול המסך; ‏stdout עונה למי שמרחוק."""
    with app.app_context():
        job = _job()

        def runner(source, vehicle, part_type, data, **_):
            trace.note("→ ScraperAPI: https://partsouq.com/en/search/all?q=X")
            raise base.FetchError("האתר החזיר 403")

        live_lookup.run_step(job, runner=runner)
    out = capsys.readouterr().out
    assert trace.MARKER in out
    assert "https://partsouq.com/en/search/all?q=X" in out
    assert "האתר החזיר 403" in out
    # השורה הראשונה היא מי הרכב ומה החלק - בלעדיה בלוק ביומן משותף
    # הוא שורות בלי בעלים.
    assert VEHICLE["vin"] in out


def test_every_stdout_line_carries_the_marker_so_it_can_be_filtered(app, capsys):
    """יומן השירות רובו שורות גישה. בלי סימן אין דרך לשלוף משם בלוק."""
    with app.app_context():
        job = _job()

        def runner(source, vehicle, part_type, data, **_):
            trace.note("שורה ראשונה")
            trace.note("שורה שנייה")
            return []

        live_lookup.run_step(job, runner=runner)
    printed = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert printed
    assert all(line.startswith(trace.MARKER) for line in printed)


def test_the_failed_stage_is_the_last_line_of_the_block(app, capsys):
    """מי שקורא יומן מרחוק רוצה את הסיבה בשורה אחת, לא בשלושים."""
    with app.app_context():
        job = _job()

        def runner(source, vehicle, part_type, data, **_):
            trace.stage("הבאת הדף", True, "HTTP 200")
            trace.stage("תוכן הדף", False, "רק 12 תווי טקסט",
                        "האתר בונה את התוכן ב-JavaScript")
            return []

        live_lookup.run_step(job, runner=runner)
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert "נעצר ב: תוכן הדף" in lines[-3]
    assert "רק 12 תווי טקסט" in lines[-3]
    # הרמז אומר מה לעשות, וזה החלק שמי שקורא מרחוק צריך
    assert "האתר בונה את התוכן ב-JavaScript" in lines[-2]


def test_a_key_that_leaks_into_a_line_does_not_reach_the_server_log(app, capsys):
    """היומן רושם כתובת יעד ולא כתובת בנויה, אבל שורה אחת שתעקוף את
    זה תדליף מפתח ליומן שקריא מהדשבורד."""
    with app.app_context():
        job = _job()

        def runner(source, vehicle, part_type, data, **_):
            trace.note("→ https://api.scraperapi.com/?api_key=SECRET123&url=x")
            return []

        live_lookup.run_step(job, runner=runner)
    out = capsys.readouterr().out
    assert "SECRET123" not in out
    assert "api_key=***" in out


def test_stdout_can_be_turned_off(app, capsys, monkeypatch):
    monkeypatch.setattr(trace, "TO_STDOUT", False)
    with app.app_context():
        job = _job()

        def runner(source, vehicle, part_type, data, **_):
            trace.note("שורה")
            return []

        live_lookup.run_step(job, runner=runner)
    assert trace.MARKER not in capsys.readouterr().out
