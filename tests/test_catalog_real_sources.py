"""Laximo ו-TecDoc: המסלול האמיתי, מול תשובות שמורות.

הבדיקות כאן לא נוגעות ברשת. הן בודקות את מה שכן בשליטתנו: איך נבנית
הבקשה, איך נחתמת, מה נשלח למודל, ומה יוצא ממנו. את מה שלא בשליטתנו -
שהאתר עונה, ושהוא עונה כמו אתמול - תופס ``scripts/catalog_probe.py``
מול השירות החי, ותשובה משם נשמרת לכאן כ-fixture.
"""
import hashlib
import json
from pathlib import Path

import pytest

from app import catalog_sources, live_lookup
from app.catalog_sources import base, browser, laximo, tecdoc

FIXTURES = Path(__file__).resolve().parent / "fixtures"

VEHICLE = {
    "plate": "12345678",
    "make": "טויוטה יפן",
    "model": "COROLLA",
    "year": 2016,
    "engine_code": "1ZR-FE",
    "model_code": "ZRE151L",
    "vin": "JTDBR32E560095678",
    "source": "data.gov.il",
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
    def __init__(self, *replies):
        self.messages = FakeMessages(replies)


# --------------------------------------------------------------------------
# Laximo
# --------------------------------------------------------------------------

def test_laximo_signs_the_command_with_the_password():
    """החתימה היא md5 של הפקודה ואחריה הסיסמה - לא להפך, ולא הסיסמה לבדה."""
    signed = laximo.sign("FindVehicleByVIN:vin=ABC", password="s3cret")
    assert signed == hashlib.md5(b"FindVehicleByVIN:vin=ABCs3cret").hexdigest()
    assert signed != hashlib.md5(b"s3cret").hexdigest()


def test_laximo_command_and_url_carry_the_vin():
    assert VEHICLE["vin"] in laximo.build_command(VEHICLE["vin"])
    assert VEHICLE["vin"] in laximo.build_web_url(VEHICLE["vin"])


def test_laximo_api_answer_becomes_a_candidate(monkeypatch):
    xml = (FIXTURES / "laximo_vin.xml").read_text(encoding="utf-8")
    sent = {}

    def fake_call(command, **kwargs):
        sent["command"] = command
        return xml

    monkeypatch.setattr(laximo, "call_api", fake_call)
    monkeypatch.setattr(laximo, "MODE", "api")
    monkeypatch.setattr(laximo, "LOGIN", "demo")
    monkeypatch.setattr(laximo, "PASSWORD", "demo")

    client = FakeClient({
        "parts": [{
            "oe_number": "04152-YZZA1",
            "name": "ELEMENT SUB-ASSY, OIL FILTER",
            "image_url": "https://img.laximo.ru/toyota/1901.png",
            "variant": "18273645",
            "confidence": "high",
            "note": "הקטלוג קושר את המק\"ט לשלדה הזו",
        }],
        "vehicle_confirmed": True,
    })
    found = laximo.LaximoSource().lookup(VEHICLE, "oil_filter", client=client)

    assert sent["command"].count(VEHICLE["vin"]) == 1
    assert len(found) == 1
    assert found[0].part_number == "04152-YZZA1"
    assert found[0].tier == "oem"
    assert found[0].oe_number == "04152-YZZA1"
    # יצרן החלק במק"ט מקורי הוא יצרן הרכב, בכתיב שהקטלוג משתמש בו
    assert found[0].manufacturer == "טויוטה"
    assert found[0].variant_key == "18273645"
    assert found[0].image_url.endswith("1901.png")

    # מה שנשלח למודל חייב לשאת את השלדה ואת שני המק"טים שבתשובה,
    # כולל זה שאינו מתאים - הפסילה שלו היא עבודה של המודל, לא שלנו
    prompt = client.messages.prompts[0]
    assert VEHICLE["vin"] in prompt
    assert "04152-YZZA1" in prompt and "90915-YZZD2" in prompt
    assert "1ZR-FE" in prompt


def test_laximo_flattens_the_xml_without_losing_fields():
    xml = (FIXTURES / "laximo_vin.xml").read_text(encoding="utf-8")
    text = base.flatten_xml(xml)
    for token in ("vehicleid=18273645", "ssd=", "04152-YZZA1", "1ZR-FE",
                  "imageurl=https://img.laximo.ru/toyota/1901.png"):
        assert token in text


def test_laximo_web_path_uses_the_browser_url(monkeypatch):
    monkeypatch.setattr(laximo, "MODE", "web")
    visited = []

    def fetcher(url, timeout=None):
        visited.append(url)
        return "<html><body>04152-YZZA1 OIL FILTER</body></html>"

    client = FakeClient({"parts": [{"oe_number": "04152-YZZA1", "confidence": "high"}]})
    found = laximo.LaximoSource().lookup(
        VEHICLE, "oil_filter", fetcher=fetcher, client=client
    )
    assert visited == [laximo.build_web_url(VEHICLE["vin"])]
    assert found[0].part_number == "04152-YZZA1"


def test_laximo_without_a_vin_does_nothing():
    assert laximo.LaximoSource().lookup(dict(VEHICLE, vin=""), "oil_filter") == []


def test_laximo_api_error_is_a_readable_failure(monkeypatch):
    monkeypatch.setattr(laximo, "MODE", "api")
    monkeypatch.setattr(laximo, "API_URL", "http://127.0.0.1:1/nope")
    monkeypatch.setattr(laximo, "LOGIN", "demo")
    monkeypatch.setattr(laximo, "PASSWORD", "demo")
    monkeypatch.setattr(laximo, "TIMEOUT", 0.2)
    with pytest.raises(base.FetchError, match="Laximo API"):
        laximo.LaximoSource().lookup(VEHICLE, "oil_filter")


# --------------------------------------------------------------------------
# TecDoc
# --------------------------------------------------------------------------

def test_tecdoc_query_searches_by_the_oem_number(monkeypatch):
    monkeypatch.setattr(tecdoc, "QUERY", "")
    monkeypatch.setattr(tecdoc, "PROVIDER", "12345")
    query = tecdoc.build_query("04152-YZZA1")["getArticles"]
    assert query["searchQuery"] == "04152-YZZA1"
    assert query["provider"] == 12345
    assert query["includeOEMNumbers"] is True


def test_tecdoc_query_template_from_the_environment(monkeypatch):
    monkeypatch.setattr(
        tecdoc, "QUERY", '{"getArticles": {"searchQuery": "{oem}", "perPage": 3}}'
    )
    assert tecdoc.build_query("ABC-1") == {
        "getArticles": {"searchQuery": "ABC-1", "perPage": 3}
    }


def test_tecdoc_api_answer_becomes_candidates(monkeypatch):
    payload = (FIXTURES / "tecdoc_articles.json").read_text(encoding="utf-8")
    monkeypatch.setattr(tecdoc, "MODE", "api")
    monkeypatch.setattr(tecdoc, "API_KEY", "k")
    monkeypatch.setattr(tecdoc, "PROVIDER", "12345")
    monkeypatch.setattr(tecdoc, "call_api", lambda oem, **kw: payload)

    client = FakeClient({
        "parts": [
            {"part_number": "W 610/3", "manufacturer": "MANN-FILTER",
             "image_url": "https://cdn.tecalliance.test/mann-w610-800.jpg",
             "confidence": "high", "note": "רשום כתחליף למספר המקורי"},
            {"part_number": "F 026 407 006", "manufacturer": "BOSCH",
             "image_url": "https://cdn.tecalliance.test/bosch-f026407006.jpg",
             "confidence": "high", "note": "רשום כתחליף למספר המקורי"},
        ]
    })
    found = tecdoc.TecDocSource().lookup(
        VEHICLE, "oil_filter", oem_numbers=["04152-YZZA1"], client=client
    )
    assert [c.part_number for c in found] == ["W 610/3", "F 026 407 006"]
    assert all(c.tier == "aftermarket" for c in found)
    assert all(c.oe_number == "04152-YZZA1" for c in found)
    assert found[0].image_url.endswith("mann-w610-800.jpg")
    assert "04152-YZZA1" in client.messages.prompts[0]
    assert "MANN-FILTER" in client.messages.prompts[0]


def test_tecdoc_needs_an_oem_number():
    assert tecdoc.TecDocSource().lookup(VEHICLE, "oil_filter") == []


def test_tecdoc_readable_survives_a_non_json_answer():
    assert "Service Unavailable" in tecdoc.readable("Service Unavailable")


# --------------------------------------------------------------------------
# החיבור בין השניים, והדפדפן
# --------------------------------------------------------------------------

def test_the_default_chain_is_laximo_then_tecdoc(monkeypatch):
    """הסדר הוא המהות: Laximo מוציא את המספר ש-TecDoc מחפש לפיו."""
    monkeypatch.delenv("CATALOG_SOURCES", raising=False)
    assert catalog_sources.enabled_keys() == ["laximo", "tecdoc"]
    assert catalog_sources.get("laximo").tier == "oem"
    assert catalog_sources.get("tecdoc").tier == "aftermarket"


def test_the_oem_number_flows_from_the_first_source_to_the_second(app):
    """זה החיבור שכל התהליך תלוי בו, ולכן הוא נבדק במפורש."""
    with app.app_context():
        seen = {}

        class Spy(tecdoc.TecDocSource):
            def lookup(self, vehicle, part_type, oem_numbers=(), **kwargs):
                seen["numbers"] = list(oem_numbers)
                return []

        data = {"results": [{"oe_number": "04152-YZZA1", "part_number": "04152-YZZA1"}]}
        live_lookup._run_source(Spy(), VEHICLE, "oil_filter", data)
        assert "04152-YZZA1" in seen["numbers"]


def test_the_catalog_contributes_oem_numbers_it_already_has(app):
    """גם בלי Laximo יש מאיפה להתחיל: מק"ט מקורי שכבר יושב בקטלוג."""
    with app.app_context():
        numbers = live_lookup.known_oem_numbers(
            dict(VEHICLE, make="טויוטה"), "brake_pads_front", []
        )
        assert "04465-02220" in numbers


def test_browser_finds_the_chromium_that_is_actually_installed(monkeypatch, tmp_path):
    """גרסת ה-build של Playwright והתמונה לא תמיד תואמות.

    כשהן לא, ``launch()`` נכשל על נתיב שלא קיים - ולכן מאתרים את הקיים
    ומעבירים אותו במפורש.
    """
    exe = tmp_path / "chromium-1194" / "chrome-linux" / "chrome"
    exe.parent.mkdir(parents=True)
    exe.write_text("#!/bin/sh\n")
    monkeypatch.delenv("PLAYWRIGHT_CHROMIUM_PATH", raising=False)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    assert browser.chromium_path() == str(exe)


def test_browser_prefers_an_explicit_path(monkeypatch, tmp_path):
    exe = tmp_path / "my-chrome"
    exe.write_text("#!/bin/sh\n")
    monkeypatch.setenv("PLAYWRIGHT_CHROMIUM_PATH", str(exe))
    assert browser.chromium_path() == str(exe)


def test_browser_can_be_turned_off(monkeypatch):
    monkeypatch.setattr(browser, "BROWSER_ENABLED", False)
    assert browser.browser_available() is False
    with pytest.raises(browser.BrowserError, match="CATALOG_BROWSER"):
        browser.fetch_page("https://example.invalid/")


def test_a_missing_browser_binary_says_how_to_install_it(monkeypatch):
    """pip install playwright לא מוריד את Chromium.

    בלי ההבחנה הזו הפיצ'ר היה נראה זמין ונופל רק בלחיצה - עם הודעה
    של Playwright במקום עם מה שצריך להקליד.
    """
    monkeypatch.setattr(browser, "BROWSER_ENABLED", True)
    monkeypatch.setattr(browser, "chromium_installed", lambda: False)
    assert browser.browser_available() is False
    with pytest.raises(browser.BrowserError, match="playwright install"):
        browser.fetch_page("https://example.invalid/")


def test_robots_is_checked_on_the_browser_path_too(monkeypatch):
    """אורח, לא בוט - וגם כשהאורח הוא Chromium."""
    monkeypatch.setattr(browser, "BROWSER_ENABLED", True)
    monkeypatch.setattr(browser, "chromium_installed", lambda: True)
    monkeypatch.setattr(base, "allowed_by_robots", lambda url, agent=None: False)
    with pytest.raises(browser.BrowserError, match="robots.txt"):
        browser.fetch_page("https://example.invalid/secret")


def test_the_browser_can_search_through_a_form(monkeypatch):
    """לא בכל קטלוג מגיעים לתוצאה בכתובת. לפעמים ממלאים שדה ולוחצים."""
    monkeypatch.setattr(browser, "BROWSER_ENABLED", True)
    monkeypatch.setattr(browser, "chromium_installed", lambda: True)
    monkeypatch.setattr(base, "allowed_by_robots", lambda url, agent=None: True)
    fetcher = browser.BrowserFetcher(
        fill_selector="#q", fill_value="JTDBR32E560095678", submit_selector="#go"
    )
    assert fetcher.fill_value == "JTDBR32E560095678"
    assert fetcher.submit_selector == "#go"


def test_a_source_that_needs_a_browser_is_unavailable_without_one(monkeypatch):
    monkeypatch.setattr(laximo, "MODE", "web")
    monkeypatch.setattr(base, "PARSE_MODEL", "x")
    monkeypatch.setattr(browser, "BROWSER_ENABLED", False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert laximo.LaximoSource().available() is False


@pytest.mark.skipif(
    not browser.browser_available(),
    reason="הדפדפן אינו מותקן (playwright install chromium)",
)
def test_the_browser_serves_requests_from_several_threads():
    """הדפדפן חייב לשרת threads שונים - כי כך gunicorn מגיש בקשות.

    ה-API הסינכרוני של Playwright קשור ל-thread שיצר אותו. בלי thread
    ייעודי שמחזיק אותו, הבקשה השנייה - שמגיעה מ-thread אחר של gthread -
    הייתה נופלת על אובייקט שאינו שלה.
    """
    import threading

    results = {}

    def fetch(index):
        try:
            html = browser.fetch_page(
                f"data:text/html,<h1>hi {index}</h1>", timeout=15
            )
            results[index] = f"hi {index}" in html
        except Exception as exc:  # pragma: no cover - נראה רק כשנשבר
            results[index] = f"{type(exc).__name__}: {exc}"

    threads = [threading.Thread(target=fetch, args=(i,)) for i in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    try:
        assert results == {0: True, 1: True, 2: True}
    finally:
        browser.shutdown()
