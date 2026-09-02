"""המקורות חייבים להשתמש בהבאה שהוגדרה, לא ב-urllib ישירות.

זה נראה כמו פרט פנימי, וזה היה התקלה: ``CATALOG_FETCHER`` הצביע על
ScraperAPI, ``epc`` ו-``aftermarket`` קראו ל-``fetch`` ישירות, הבקשה
יצאה מכתובת הענן של Railway, ו-Cloudflare החזיר 403. ‏Laximo ו-TecDoc
עשו את זה נכון מהיום הראשון, ולכן הפער לא נראה עד שהופעלו דווקא
שני המקורות האחרים.
"""
import pytest

from app.catalog_sources import aftermarket, base, epc_vin, scraperapi, trace

from test_catalog_real_sources import FakeClient

VEHICLE = {"make": "טויוטה יפן", "model": "COROLLA", "year": 2011,
           "vin": "JTNBV58E20J147563", "engine_code": "1ZR", "plate": "1234567"}

PAGE = "<html><head><title>Toyota Corolla</title></head><body>x</body></html>"


@pytest.fixture
def through_scraperapi(monkeypatch):
    """מגדיר ScraperAPI כמסלול, ומקליט מה עבר דרכו."""
    seen = []

    def call(self, url, timeout=None):
        seen.append(url)
        base.describe_page(PAGE, url, final_url=url, status=200)
        return PAGE

    monkeypatch.setattr(base, "FETCHER", "scraperapi")
    monkeypatch.setattr(scraperapi, "API_KEY", "test-key")
    monkeypatch.setattr(scraperapi.ScraperApiFetcher, "__call__", call)
    return seen


def test_the_vin_source_goes_through_the_configured_fetcher(through_scraperapi):
    epc_vin.EpcVinSource().lookup(
        VEHICLE, "fuel_pump",
        client=FakeClient({"parts": [], "next_url": "", "vehicle_confirmed": True}),
    )
    assert through_scraperapi, "המקור עקף את ההבאה שהוגדרה ויצא ישירות"
    assert VEHICLE["vin"] in through_scraperapi[0]


def test_the_aftermarket_source_goes_through_the_configured_fetcher(
    through_scraperapi,
):
    aftermarket.AftermarketSource().lookup(
        VEHICLE, "fuel_pump", oem_numbers=["23220-0T030"],
        client=FakeClient({"parts": []}),
    )
    assert through_scraperapi, "המקור עקף את ההבאה שהוגדרה ויצא ישירות"
    assert "23220-0T030" in through_scraperapi[0]


def test_an_injected_fetcher_still_wins(through_scraperapi):
    """בדיקות ו-catalog_probe מזריקות הבאה משלהן, וזה חייב להישאר."""
    mine = []

    def fetcher(url, timeout=None):
        mine.append(url)
        return PAGE

    epc_vin.EpcVinSource().lookup(
        VEHICLE, "fuel_pump", fetcher=fetcher,
        client=FakeClient({"parts": [], "next_url": ""}),
    )
    assert mine and not through_scraperapi


def test_the_log_names_the_fetcher_that_actually_ran(through_scraperapi):
    """שורת היומן הקודמת דיווחה על ההגדרה, לא על מה שקרה - וזה בדיוק
    מה שהאריך את החקירה הזו."""
    trace.start()
    epc_vin.EpcVinSource().lookup(
        VEHICLE, "fuel_pump",
        client=FakeClient({"parts": [], "next_url": ""}),
    )
    assert "הבאה: ScraperApiFetcher" in "\n".join(trace.lines())


def test_a_direct_fetch_is_named_as_such(monkeypatch):
    assert base.fetcher_name(base.fetch) == "ישירה (urllib)"
    assert base.fetcher_name(scraperapi.ScraperApiFetcher()) == "ScraperApiFetcher"
