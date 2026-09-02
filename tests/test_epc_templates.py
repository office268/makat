"""כתובת החיפוש של מקור OEM: איך יודעים שהיא שגויה, ואיך בודקים כמה.

הריצה שחשפה את זה: ``https://7zap.com/en/search/?q={vin}`` החזירה
‏HTTP 200 ודף תקין לגמרי - דף הבית של האתר. המשתמש ראה "לא מצא מק"ט
לחלק הזה ברכב הזה", שהוא לא רק לא נכון אלא גם כיוון שאי אפשר לפעול
לפיו: החלק קיים, הכתובת שגויה.
"""
import pytest

from app.catalog_sources import base, epc_vin, trace
from app.catalog_sources.base import Continuation

from test_catalog_real_sources import FakeClient

VEHICLE = {"make": "פיג'ו צרפת", "model": "5008", "year": 2020,
           "vin": "VF3M45GFRLS125956", "engine_code": "5G06", "plate": "1234567"}

# שתי התשובות הריקות האלה נראות זהות, והן הפוכות במשמעותן: האחת
# אומרת "הכרתי את הרכב, החלק הזה לא אצלי" - תשובה, ששווה לשמור.
# השנייה אומרת "לא הכרתי את הרכב בכלל" - כלומר האתר אינו מכסה אותו,
# וזו לא תשובה שמותר לשמור במטמון כשלילית.
NOT_IN_CATALOG = {"parts": [], "next_url": "", "vehicle_confirmed": True}
UNKNOWN_CAR = {"parts": [], "next_url": "", "vehicle_confirmed": False}
NOTHING = NOT_IN_CATALOG
SOMETHING = {"parts": [{"oe_number": "1525 QN", "name": "Fuel pump",
                        "confidence": "high"}],
             "next_url": "", "vehicle_confirmed": True}


def _page(final_url=None):
    """הבאה מדומה שמדווחת לאן נחתה - בדיוק כמו ההבאות האמיתיות."""
    def fetcher(url, timeout=None):
        html = "<html><head><title>דף</title></head><body>תוכן</body></html>"
        base.describe_page(html, url, final_url=final_url or url, status=200)
        return html
    return fetcher


# --------------------------------------------------------------------------
# זיהוי ההקפצה
# --------------------------------------------------------------------------

@pytest.mark.parametrize("requested,landed,bounced", [
    # מה שקרה בשטח: חיפוש שלדה שנדחף לדף הבית
    ("https://7zap.com/en/search/?q=VIN", "https://7zap.com/en/", True),
    # השאילתה שנשאה את השלדה נבלעה, הנתיב נשאר
    ("https://7zap.com/en/search/?q=VIN", "https://7zap.com/en/search/", True),
    ("https://a.com/vin/X/", "https://a.com/", True),
    # הפניה שמעמיקה לתוך הקטלוג היא חיפוש שהצליח, לא הקפצה
    ("https://7zap.com/en/search/?q=VIN",
     "https://7zap.com/en/catalog/peugeot/5008/", False),
    ("https://a.com/vin/X/", "https://a.com/vin/X/result/?id=3", False),
    # בלי הפניה בכלל
    ("https://7zap.com/en/search/?q=VIN", "https://7zap.com/en/search/?q=VIN", False),
    # מארח אחר אינו "אב" של הכתובת שביקשנו
    ("https://7zap.com/en/search/?q=VIN", "https://other.com/en/", False),
])
def test_only_a_redirect_that_widens_counts_as_a_bounce(requested, landed, bounced):
    assert base.bounced_to_ancestor(requested, landed) is bounced


def test_where_a_fetch_landed_does_not_leak_into_the_next_one():
    base.describe_page("<html></html>", "https://a.com/x/",
                       final_url="https://a.com/")
    assert base.landed_at() == "https://a.com/"
    base.describe_page("<html></html>", "https://a.com/y/")
    assert base.landed_at() == "https://a.com/y/"


# --------------------------------------------------------------------------
# התבניות
# --------------------------------------------------------------------------

def test_several_templates_are_split_and_trimmed(monkeypatch):
    monkeypatch.setattr(epc_vin, "URL_TEMPLATE",
                        " https://a.com/?q={vin} | https://b.com/{vin}/ ||")
    assert epc_vin.templates() == ["https://a.com/?q={vin}", "https://b.com/{vin}/"]
    assert epc_vin.build_urls("ABC") == ["https://a.com/?q=ABC", "https://b.com/ABC/"]
    # ‏build_url נשארה הכתובת הראשונה, כדי שקוראים ותיקים לא ישתנו
    assert epc_vin.build_url("ABC") == "https://a.com/?q=ABC"


def test_a_template_without_a_vin_placeholder_is_flagged(monkeypatch):
    """כתובת בלי השלדה לא יכולה להחזיר תשובה *לרכב הזה*."""
    monkeypatch.setattr(epc_vin, "URL_TEMPLATE", "https://a.com/catalog/")
    trace.start()
    epc_vin.EpcVinSource().lookup(
        VEHICLE, "fuel_pump", fetcher=_page(), client=FakeClient(NOTHING)
    )
    assert "תבנית בלי {vin}" in "\n".join(trace.lines())


# --------------------------------------------------------------------------
# ההתנהגות
# --------------------------------------------------------------------------

def test_a_bounce_is_a_configuration_error_not_an_empty_answer(monkeypatch):
    """זה הלב: 'לא נמצא' ו'הכתובת שגויה' חייבים להיראות אחרת."""
    monkeypatch.setattr(epc_vin, "URL_TEMPLATE", "https://7zap.com/en/search/?q={vin}")
    trace.start()
    with pytest.raises(base.FetchError) as caught:
        epc_vin.EpcVinSource().lookup(
            VEHICLE, "fuel_pump",
            fetcher=_page(final_url="https://7zap.com/en/"),
            client=FakeClient(NOTHING),
        )
    assert "EPC_VIN_URL" in str(caught.value)
    assert "https://7zap.com/en/" in str(caught.value)


def test_a_bounce_does_not_cost_a_model_call(monkeypatch):
    """הדף כבר ידוע כלא רלוונטי. אין סיבה לשלם עליו קריאה למודל."""
    monkeypatch.setattr(epc_vin, "URL_TEMPLATE", "https://7zap.com/en/search/?q={vin}")
    client = FakeClient(NOTHING)
    with pytest.raises(base.FetchError):
        epc_vin.EpcVinSource().lookup(
            VEHICLE, "fuel_pump",
            fetcher=_page(final_url="https://7zap.com/en/"), client=client,
        )
    assert client.messages.prompts == []


def test_the_second_template_runs_when_the_first_bounces(monkeypatch):
    monkeypatch.setattr(
        epc_vin, "URL_TEMPLATE",
        "https://7zap.com/en/search/?q={vin}|https://good.com/vin/{vin}/",
    )

    def fetcher(url, timeout=None):
        html = "<html><body>x</body></html>"
        landed = "https://7zap.com/en/" if "7zap" in url else url
        base.describe_page(html, url, final_url=landed, status=200)
        return html

    trace.start()
    found = epc_vin.EpcVinSource().lookup(
        VEHICLE, "fuel_pump", fetcher=fetcher, client=FakeClient(SOMETHING)
    )
    assert [c.part_number for c in found] == ["1525 QN"]
    log = "\n".join(trace.lines())
    assert "— תבנית 1/2" in log and "— תבנית 2/2" in log
    assert "התבנית הזו לא עבדה" in log
    # הכתובת שנשמרת עם המק"ט היא זו שבאמת ענתה
    assert found[0].source_url == f"https://good.com/vin/{VEHICLE['vin']}/"


def test_a_template_that_answered_nothing_beats_one_that_failed(monkeypatch):
    """'האתר ענה, אין כאן כזה חלק' היא תשובה. תבנית שבורה לא תסתיר אותה."""
    monkeypatch.setattr(epc_vin, "URL_TEMPLATE",
                        "https://dead.com/{vin}|https://alive.com/{vin}")

    def fetcher(url, timeout=None):
        if "dead" in url:
            raise base.FetchError("האתר החזיר 404")
        html = "<html><body>x</body></html>"
        base.describe_page(html, url, final_url=url, status=200)
        return html

    found = epc_vin.EpcVinSource().lookup(
        VEHICLE, "fuel_pump", fetcher=fetcher, client=FakeClient(NOTHING)
    )
    assert found == []


def test_when_every_template_fails_it_is_a_failure(monkeypatch):
    monkeypatch.setattr(epc_vin, "URL_TEMPLATE",
                        "https://a.com/{vin}|https://b.com/{vin}")

    def fetcher(url, timeout=None):
        raise base.FetchError(f"האתר החזיר 404 עבור {url}")

    with pytest.raises(base.FetchError) as caught:
        epc_vin.EpcVinSource().lookup(
            VEHICLE, "fuel_pump", fetcher=fetcher, client=FakeClient(NOTHING)
        )
    # השגיאה הראשונה, לא האחרונה - היא של התבנית שנבחרה קודם
    assert "https://a.com/" in str(caught.value)


def test_no_template_at_all_says_which_variable_is_missing(monkeypatch):
    monkeypatch.setattr(epc_vin, "URL_TEMPLATE", "  ")
    with pytest.raises(base.FetchError) as caught:
        epc_vin.EpcVinSource().lookup(
            VEHICLE, "fuel_pump", fetcher=_page(), client=FakeClient(NOTHING)
        )
    assert "EPC_VIN_URL" in str(caught.value)


def test_the_follow_up_rules_forbid_a_generic_brand_page():
    """מה שקרה בשטח: המודל הציע ללכת לדף המותג, ומשם לכלום."""
    prompt = epc_vin.build_prompt(VEHICLE, "fuel_pump", "דף", "https://a.com/", 1)
    assert "דף מותג כללי" in prompt
    assert "אל תחזיר את דף הבית" in prompt
    # בצעד האחרון אין המשך, ולכן גם אין את הכללים שלו
    last = epc_vin.build_prompt(VEHICLE, "fuel_pump", "דף", "https://a.com/", 0)
    assert "דף מותג כללי" not in last


# --------------------------------------------------------------------------
# "לא הכרתי את הרכב" אינו "אין כאן כזה חלק"
# --------------------------------------------------------------------------

def test_a_catalog_that_does_not_cover_the_vehicle_is_not_an_empty_answer(monkeypatch):
    """הכישלון השקט השני, וזה שנכנס דרך *בחירת האתר* ולא דרך הכתובת.

    קטלוג שמכסה יצרנים אחרים מחזיר 200 ודף תקין לכל שלדה שאינה שלו.
    בלי ההבחנה הזו התשובה "אין מק"ט לרכב הזה" נשמרת לחודשיים - על רכב
    שהאתר מעולם לא הכיר.
    """
    monkeypatch.setattr(epc_vin, "URL_TEMPLATE", "https://a.com/?q={vin}")
    with pytest.raises(base.FetchError) as caught:
        epc_vin.EpcVinSource().lookup(
            VEHICLE, "fuel_pump", fetcher=_page(), client=FakeClient(UNKNOWN_CAR)
        )
    assert "לא זיהה את הרכב" in str(caught.value)
    # השגיאה אומרת איזה יצרן חסר, כדי שאפשר יהיה לפעול לפיה
    assert "פיג'ו" in str(caught.value)
    assert "EPC_VIN_URL" in str(caught.value)


def test_a_page_that_knew_the_car_and_had_no_part_stays_an_answer(monkeypatch):
    """הצד השני של אותה הבחנה - אחרת לא היינו שומרים אף תשובה שלילית."""
    monkeypatch.setattr(epc_vin, "URL_TEMPLATE", "https://a.com/?q={vin}")
    assert epc_vin.EpcVinSource().lookup(
        VEHICLE, "fuel_pump", fetcher=_page(), client=FakeClient(NOT_IN_CATALOG)
    ) == []


def test_one_template_that_knew_the_car_rescues_the_whole_lookup(monkeypatch):
    """תבנית אחת שזיהתה את הרכב מספיקה - אתר אחד שמכיר אותו הוא הכל."""
    monkeypatch.setattr(epc_vin, "URL_TEMPLATE",
                        "https://blind.com/{vin}|https://knows.com/{vin}")
    client = FakeClient(UNKNOWN_CAR, NOT_IN_CATALOG)
    assert epc_vin.EpcVinSource().lookup(
        VEHICLE, "fuel_pump", fetcher=_page(), client=client
    ) == []
    assert len(client.messages.prompts) == 2


def test_finding_a_part_needs_no_confirmation(monkeypatch):
    """מק"ט שנמצא הוא ההוכחה החזקה ביותר שהאתר מכיר את הרכב."""
    monkeypatch.setattr(epc_vin, "URL_TEMPLATE", "https://a.com/?q={vin}")
    found = epc_vin.EpcVinSource().lookup(
        VEHICLE, "fuel_pump", fetcher=_page(),
        client=FakeClient({"parts": [{"oe_number": "1525 QN"}], "next_url": "",
                           "vehicle_confirmed": False}),
    )
    assert [c.part_number for c in found] == ["1525 QN"]


# --------------------------------------------------------------------------
# ההבחנה חייבת לשרוד מסע שנפרס על כמה בקשות
# --------------------------------------------------------------------------

def test_a_paused_walk_is_not_judged_yet(monkeypatch):
    """מסע שנעצר בתקציב הזמן אינו מסקנה - העמוד שיזהה את הרכב עוד לפניו."""
    monkeypatch.setattr(epc_vin, "MAX_HOPS", 4)
    monkeypatch.setattr(epc_vin, "URL_TEMPLATE", "https://a.com/?q={vin}")
    resume = Continuation()
    found = epc_vin.EpcVinSource().lookup(
        VEHICLE, "fuel_pump", fetcher=_page(),
        client=FakeClient({"parts": [], "next_url": "https://a.com/next/",
                           "vehicle_confirmed": False}),
        resume=resume, deadline=base.time.monotonic() - 1,
    )
    assert found == [] and resume.url, "היה אמור להיעצר, לא להסיק"


def test_identification_survives_the_pause(monkeypatch):
    """העמוד שזיהה את הרכב כבר שולם עליו בבקשה קודמת.

    בלי שההבחנה תיסחב ב-Continuation, מסע ארוך היה מגיע לסופו בלי
    זכר לזיהוי - ונזרק כתקלה. וזה הרוב: בקטלוג אמיתי כל מסע נפרס.
    """
    monkeypatch.setattr(epc_vin, "MAX_HOPS", 4)
    resume = Continuation(url="https://a.com/group/", hop=1, identified=True)
    found = epc_vin.EpcVinSource().lookup(
        VEHICLE, "fuel_pump", fetcher=_page(),
        client=FakeClient({"parts": [], "next_url": "", "vehicle_confirmed": False}),
        resume=resume, deadline=base.time.monotonic() + 300,
    )
    assert found == [], "זו תשובה אמיתית: הרכב זוהה קודם, החלק לא שם"


def test_a_resumed_walk_that_never_knew_the_car_is_still_a_failure(monkeypatch):
    """החור שהיה ב-PR #80: הבדיקה חלה רק על מסע שהסתיים בבקשה אחת,
    ולכן דילגה בדיוק על המקרה הנפוץ."""
    monkeypatch.setattr(epc_vin, "MAX_HOPS", 4)
    resume = Continuation(url="https://a.com/group/", hop=1, identified=False)
    with pytest.raises(base.FetchError) as caught:
        epc_vin.EpcVinSource().lookup(
            VEHICLE, "fuel_pump", fetcher=_page(),
            client=FakeClient({"parts": [], "next_url": "",
                               "vehicle_confirmed": False}),
            resume=resume, deadline=base.time.monotonic() + 300,
        )
    assert "לא זיהה את הרכב" in str(caught.value)


def test_the_pause_writes_the_identification_forward(monkeypatch):
    """מה שנקבע לפני העצירה נשמר להמשך, ולא נולד מחדש."""
    monkeypatch.setattr(epc_vin, "MAX_HOPS", 4)
    monkeypatch.setattr(epc_vin, "URL_TEMPLATE", "https://a.com/?q={vin}")
    resume = Continuation()
    epc_vin.EpcVinSource().lookup(
        VEHICLE, "fuel_pump", fetcher=_page(),
        client=FakeClient({"parts": [], "next_url": "https://a.com/next/",
                           "vehicle_confirmed": True}),
        resume=resume, deadline=base.time.monotonic() - 1,
    )
    assert resume.identified is True
    resume.clear()
    assert resume.identified is False, "clear מאפס גם אותה"
