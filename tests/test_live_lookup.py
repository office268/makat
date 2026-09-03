"""שליפה חיה: מספר שלדה -> מק"ט מקורי -> מק"טים חלופיים.

כל מה שכאן רץ בלי רשת ובלי מפתח API: הדפים מגיעים מ-fixtures, המודל
מוזרק, והמקור המדומה מכסה את הצנרת מקצה לקצה. זו לא התחכמות - זה
התנאי לכך שהבדיקות ירוצו בכל סביבה, וגם מה שיתפוס שינוי מבנה באתר
כשנשמור fixture חדש לצידו.
"""
import json
from pathlib import Path

import pytest

from app import catalog_sources, live_lookup
from app.catalog_sources import aftermarket, base, epc_vin
from app.models import Part, db

FIXTURES = Path(__file__).resolve().parent / "fixtures"

VEHICLE = {
    "plate": "12345678",
    "make": "טויוטה יפן",
    "model": "COROLLA",
    "year": 2016,
    "engine_code": "1ZR-FE",
    "vin": "JTDBR32E560095678",
    "source": "offline",
}


class FakeBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class FakeMessages:
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def create(self, **kwargs):
        self.prompts.append(kwargs["messages"][0]["content"])
        payload = self.replies.pop(0) if self.replies else {}
        return type("Reply", (), {"content": [FakeBlock(json.dumps(payload))]})()


class FakeClient:
    """לקוח Anthropic מזויף: מחזיר תשובות מוכנות, וזוכר מה נשאל."""

    def __init__(self, *replies):
        self.messages = FakeMessages(replies)


def fixture_fetcher(name):
    text = (FIXTURES / name).read_text(encoding="utf-8")
    return lambda url, timeout=None: text


# --------------------------------------------------------------------------
# מפתח המטמון
# --------------------------------------------------------------------------

def test_vin_key_ignores_the_serial_number():
    """שני רכבים מאותו דגם ומאותה שנה = מפתח אחד.

    זה גם מה שמייצר פגיעות מטמון, וגם מה שמונע שמירת מזהה של רכב מסוים.
    """
    first = dict(VEHICLE, vin="JTDBR32E560095678")
    second = dict(VEHICLE, vin="JTDBR32E560099999")
    assert live_lookup.vin_key(first) == live_lookup.vin_key(second)
    assert "095678" not in live_lookup.vin_key(first)


def test_vin_key_separates_model_year():
    older = dict(VEHICLE, vin="JTDBR32E5D0095678")
    newer = dict(VEHICLE, vin="JTDBR32E560095678")
    assert live_lookup.vin_key(older) != live_lookup.vin_key(newer)


def test_vin_key_falls_back_to_the_registry_identity():
    key = live_lookup.vin_key(dict(VEHICLE, vin=""))
    assert key.startswith("reg:")
    assert "COROLLA" in key
    assert "1ZR-FE" in key


# --------------------------------------------------------------------------
# צמצום הדף
# --------------------------------------------------------------------------

def test_condense_drops_scripts_and_keeps_images_and_links():
    html = (FIXTURES / "epc_vin_page.html").read_text(encoding="utf-8")
    text = base.condense(html, "https://example.test/en/search")
    assert "noise that must not reach the model" not in text
    assert "color:red" not in text
    assert "04152-YZZA1" in text
    assert "[IMG https://example.test/img/diagram-1901.png | oil filter diagram]" in text
    assert "[LINK https://example.test/en/toyota/corolla/zre151/1901-oil-filter]" in text


def test_condense_survives_broken_html():
    assert "hello" in base.condense("<div><p>hello</div", "https://example.test/")


# --------------------------------------------------------------------------
# המקורות
# --------------------------------------------------------------------------

def test_epc_source_reads_the_oem_number_and_the_diagram():
    client = FakeClient({
        "parts": [{
            "oe_number": "04152-YZZA1",
            "name": "מסנן שמן",
            "image_url": "https://example.test/img/diagram-1901.png",
            "variant": "1901",
            "confidence": "high",
            "note": "העמוד מציג את הקבוצה של השלדה הזו",
        }],
        "vehicle_confirmed": True,
    })
    found = epc_vin.EpcVinSource().lookup(
        VEHICLE, "oil_filter",
        fetcher=fixture_fetcher("epc_vin_page.html"), client=client,
    )
    assert len(found) == 1
    assert found[0].part_number == "04152-YZZA1"
    assert found[0].tier == "oem"
    # יצרן החלק במק"ט מקורי הוא יצרן הרכב, בכתיב שהקטלוג משתמש בו
    assert found[0].manufacturer == "טויוטה"
    assert found[0].image_url.endswith("diagram-1901.png")
    # מספר השלדה חייב להגיע למודל - הוא כל העניין
    assert VEHICLE["vin"] in client.messages.prompts[0]


def test_epc_source_without_a_vin_does_nothing():
    assert epc_vin.EpcVinSource().lookup(dict(VEHICLE, vin=""), "oil_filter") == []


def test_epc_source_follows_one_link_when_the_first_page_is_empty():
    client = FakeClient(
        {"parts": [], "next_url": "https://example.test/group/1901"},
        {"parts": [{"oe_number": "04152-YZZA1", "confidence": "high"}]},
    )
    visited = []

    def fetcher(url, timeout=None):
        visited.append(url)
        return (FIXTURES / "epc_vin_page.html").read_text(encoding="utf-8")

    found = epc_vin.EpcVinSource().lookup(
        VEHICLE, "oil_filter", fetcher=fetcher, client=client
    )
    assert [candidate.part_number for candidate in found] == ["04152-YZZA1"]
    assert visited[1] == "https://example.test/group/1901"


def test_epc_source_jumps_straight_to_the_group_diagram():
    """עמוד רכב של קטלוג טויוטה: הקבוצה ידועה, ולא שואלים עליה את המודל.

    המודל מציע ללכת לקטגוריה (שסי), והחלק המבוקש הוא מסנן שמן שיושב
    בקטגוריה אחרת לגמרי. הקיצור מתקן את שתיהן, וחוסך את שני צעדי
    הביניים שהמסע הרגיל היה משלם עליהם בבקשה וקריאת מודל כל אחד.
    """
    vehicle_url = (
        "https://partsouq.com/en/catalog/toyota/vehicle/NA/2015/RAV4-JPP/"
        "ASA44L-ANTGKA/category/2/vin/JTDBR32E560095678"
    )
    client = FakeClient(
        {"parts": [], "next_url": vehicle_url, "vehicle_confirmed": True},
        {"parts": [{"oe_number": "04152-YZZA1", "confidence": "high"}]},
    )
    visited = []

    def fetcher(url, timeout=None):
        visited.append(url)
        return (FIXTURES / "epc_vin_page.html").read_text(encoding="utf-8")

    found = epc_vin.EpcVinSource().lookup(
        VEHICLE, "oil_filter", fetcher=fetcher, client=client
    )
    assert [candidate.part_number for candidate in found] == ["04152-YZZA1"]
    assert visited[1] == (
        "https://partsouq.com/en/catalog/toyota/diagram/NA/2015/RAV4-JPP/"
        "ASA44L-ANTGKA/category/1/diagram/1502/vin/JTDBR32E560095678"
    )
    # שתי הבאות בסך הכול: החיפוש, ואז התרשים עצמו
    assert len(visited) == 2


def test_epc_source_keeps_following_the_model_when_there_is_no_shortcut():
    """קטלוג שאינו טויוטה אינו מקבל ניחוש קבוצה - הוא ממשיך כרגיל."""
    other = "https://partsouq.com/en/catalog/kia/vehicle/NA/2015/RIO/X/category/2"
    client = FakeClient(
        {"parts": [], "next_url": other},
        {"parts": [{"oe_number": "26300-35505", "confidence": "high"}]},
    )
    visited = []

    def fetcher(url, timeout=None):
        visited.append(url)
        return (FIXTURES / "epc_vin_page.html").read_text(encoding="utf-8")

    epc_vin.EpcVinSource().lookup(
        VEHICLE, "oil_filter", fetcher=fetcher, client=client
    )
    assert visited[1] == other


def test_aftermarket_source_searches_by_the_oem_number():
    client = FakeClient({
        "parts": [
            {"part_number": "W 610/3", "manufacturer": "MANN-FILTER",
             "image_url": "https://cdn.example.test/mann-w610.jpg",
             "price_listed": 4.99, "confidence": "high", "note": "מופיע כתחליף"},
        ]
    })
    found = aftermarket.AftermarketSource().lookup(
        VEHICLE, "oil_filter", oem_numbers=["04152-YZZA1"],
        fetcher=fixture_fetcher("aftermarket_page.html"), client=client,
    )
    assert found[0].part_number == "W 610/3"
    assert found[0].oe_number == "04152-YZZA1"
    assert found[0].tier == "aftermarket"
    assert "04152-YZZA1" in client.messages.prompts[0]


def test_aftermarket_source_needs_an_oem_number():
    assert aftermarket.AftermarketSource().lookup(VEHICLE, "oil_filter") == []


def test_registry_order_is_configurable(monkeypatch):
    monkeypatch.setenv("CATALOG_SOURCES", "aftermarket,epc")
    assert catalog_sources.enabled_keys() == ["aftermarket", "epc"]
    monkeypatch.setenv("CATALOG_SOURCES", "mock,אין-כזה")
    assert catalog_sources.enabled_keys() == ["mock"]


# --------------------------------------------------------------------------
# העבודה, מקצה לקצה מול המקור המדומה
# --------------------------------------------------------------------------

@pytest.fixture
def mock_sources(monkeypatch):
    monkeypatch.setenv("CATALOG_SOURCES", "mock")
    return catalog_sources.enabled_sources()


def _drain(job):
    """מריץ את כל השלבים, כמו שהדפדפן עושה - בקשה לכל שלב."""
    guard = 0
    while job.is_running and guard < 10:
        live_lookup.run_step(job)
        guard += 1
    return job


def test_job_runs_saves_and_caches(app, mock_sources):
    with app.app_context():
        job = live_lookup.start_job(VEHICLE, "oil_filter")
        assert job.total == 1
        _drain(job)

        assert job.status == live_lookup.LookupJob.DONE
        payload = job.result_data
        numbers = [row["part_number"] for row in payload["results"]]
        assert numbers, 'המקור המדומה חייב להחזיר לפחות מק"ט אחד מאומת'

        # מה שאומת נכנס לקטלוג, עם התאמה מדויקת לרכב ועם תמונה
        part = Part.query.filter_by(part_number=numbers[0]).first()
        assert part is not None
        assert part.image_url
        assert live_lookup.SOURCE_MARK in part.notes
        fitment = part.fitments[0]
        assert fitment.make == "טויוטה"
        assert fitment.engine_code == "1ZR-FE"
        assert fitment.year_from == 2016

        # מה שלא אומת מוצג ומסומן, ולא נכנס לקטלוג
        unverified = payload["unverified"]
        assert unverified and unverified[0]["verified"] is False
        assert unverified[0]["reason"]
        assert Part.query.filter_by(
            part_number=unverified[0]["part_number"]
        ).first() is None

        # והתשובה נשמרה למי שישאל אותה שוב
        assert live_lookup.cached(VEHICLE, "oil_filter") is not None


def test_a_cached_answer_serves_a_different_car_of_the_same_model(app, mock_sources):
    with app.app_context():
        _drain(live_lookup.start_job(VEHICLE, "oil_filter"))
        twin = dict(VEHICLE, plate="87654321", vin="JTDBR32E560011111")
        assert live_lookup.cached(twin, "oil_filter") is not None
        assert live_lookup.cached(twin, "air_filter") is None


def test_stale_cache_is_ignored(app, mock_sources):
    with app.app_context():
        _drain(live_lookup.start_job(VEHICLE, "oil_filter"))
        assert live_lookup.cached(VEHICLE, "oil_filter", max_age_days=0) is None


def test_read_only_shows_results_but_writes_nothing(app, mock_sources):
    """נעילת הכתיבה מגנה על הקטלוג, לא מכבה את זרימת הזיהוי."""
    with app.app_context():
        app.config["READ_ONLY"] = True
        try:
            job = _drain(live_lookup.start_job(VEHICLE, "brake_pads_front"))
            results = job.result_data["results"]
            assert results
            assert Part.query.filter_by(
                part_number=results[0]["part_number"]
            ).first() is None
        finally:
            app.config["READ_ONLY"] = False


def test_a_failing_source_does_not_kill_the_job(app, mock_sources):
    with app.app_context():
        job = live_lookup.start_job(VEHICLE, "oil_filter")

        def explode(source, vehicle, part_type, data, **_):
            raise base.FetchError("האתר לא נגיש")

        live_lookup.run_step(job, runner=explode)
        assert job.status == live_lookup.LookupJob.DONE
        assert "האתר לא נגיש" in job.error


def test_a_failed_lookup_is_not_remembered(app, mock_sources):
    """תקלת רשת לא הופכת ל"אין כזה חלק" לחודשיים."""
    with app.app_context():
        job = live_lookup.start_job(VEHICLE, "oil_filter")

        def explode(source, vehicle, part_type, data, **_):
            raise base.FetchError("האתר לא נגיש")

        live_lookup.run_step(job, runner=explode)
        assert job.status == live_lookup.LookupJob.DONE
        assert live_lookup.cached(VEHICLE, "oil_filter") is None

        # ואילו "רצנו ולא מצאנו" כן נשמר - הוא חוסך את החיפוש הבא
        empty = live_lookup.start_job(VEHICLE, "air_filter")
        live_lookup.run_step(empty, runner=lambda *a, **k: [])
        assert live_lookup.cached(VEHICLE, "air_filter") is not None


def test_unknown_part_type_is_refused(app, mock_sources):
    with app.app_context():
        with pytest.raises(ValueError):
            live_lookup.start_job(VEHICLE, "אין-כזה-חלק")


def test_quota_stops_a_runaway_loop(app, mock_sources, monkeypatch, org_id):
    with app.app_context():
        monkeypatch.setattr(live_lookup, "DAILY_LIMIT", 1)
        user = type("U", (), {"id": None, "organization_id": org_id})()
        live_lookup.start_job(VEHICLE, "oil_filter", user=user)
        with pytest.raises(ValueError, match="תקרת"):
            live_lookup.start_job(VEHICLE, "air_filter", user=user)


# --------------------------------------------------------------------------
# המסך
# --------------------------------------------------------------------------

def test_the_screen_offers_the_lookup_once_a_vehicle_is_identified(
    client, mock_sources, monkeypatch
):
    from app import vehicles

    monkeypatch.setattr(vehicles, "lookup_online", lambda plate: None)
    page = client.post("/", data={"plate": "12345678", "action": "vehicle"})
    body = page.get_data(as_text=True)
    assert "חיפוש לפי מספר שלדה" in body
    assert "חפש אצל היצרן" in body


def test_the_catalog_answers_before_anyone_goes_out_to_the_network(
    client, mock_sources, monkeypatch
):
    """לרפידות קדמיות יש מק'ט ב-fixture, ולכן אין סיבה לצאת לאתר."""
    from app import vehicles

    monkeypatch.setattr(vehicles, "lookup_online", lambda plate: None)
    response = client.post(
        "/lookup/start",
        data={"plate": "12345678", "part_type": "brake_pads_front"},
    )
    payload = response.get_json()
    assert payload["from_catalog"] is True
    assert payload["results"][0]["part_number"] == "TEST-001"
    assert payload["job"] is None


def test_the_screen_runs_a_lookup_step_by_step(client, mock_sources, monkeypatch):
    from app import vehicles

    monkeypatch.setattr(vehicles, "lookup_online", lambda plate: None)
    started = client.post(
        "/lookup/start", data={"plate": "12345678", "query": "פילטר שמן"}
    ).get_json()
    assert started["from_cache"] is False
    job = started["job"]
    assert job["part_type"] == "oil_filter"

    while job["is_running"]:
        job = client.post("/lookup/step", data={"job": job["id"]}).get_json()["job"]
    assert job["results"]
    assert job["status"] == "done"

    # אותה שאלה שנייה נענית מהקטלוג: מה שנשלף כבר נשמר שם, וזו כל
    # הנקודה - הקטלוג גדל מכל חיפוש, והחיפוש הבא לא עולה כלום.
    again = client.post(
        "/lookup/start", data={"plate": "12345678", "part_type": "oil_filter"},
    ).get_json()
    assert again["from_catalog"] is True
    assert job["results"][0]["part_number"] in [
        row["part_number"] for row in again["results"]
    ]

    # "חפש בכל זאת אצל היצרן" מדלג על הקטלוג - ונופל על התשובה
    # השמורה, בלי בקשת רשת חדשה ובלי עבודה חדשה.
    forced = client.post(
        "/lookup/start",
        data={"plate": "12345678", "part_type": "oil_filter", "force": "1"},
    ).get_json()
    assert forced["from_cache"] is True
    assert forced["job"] is None


def test_an_unknown_plate_is_refused(client, mock_sources, monkeypatch):
    from app import vehicles

    monkeypatch.setattr(vehicles, "lookup_online", lambda plate: None)
    response = client.post("/lookup/start", data={"plate": "00000000"})
    assert response.status_code == 404


def test_a_lookup_without_a_part_is_refused(client, mock_sources, monkeypatch):
    from app import vehicles

    monkeypatch.setattr(vehicles, "lookup_online", lambda plate: None)
    response = client.post(
        "/lookup/start", data={"plate": "12345678", "query": "אבטיח בטעם תות"}
    )
    assert response.status_code == 400


def test_the_screen_says_so_when_the_lookup_is_off(client, monkeypatch):
    """בלי מפתח ובלי מקור, הכרטיס מסביר למה - ולא נעלם בשקט."""
    from app import vehicles

    monkeypatch.setenv("CATALOG_SOURCES", "epc,aftermarket")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(vehicles, "lookup_online", lambda plate: None)
    body = client.post(
        "/", data={"plate": "12345678", "action": "vehicle"}
    ).get_data(as_text=True)
    assert "השליפה החיה כבויה" in body
    assert "חפש אצל היצרן" not in body
