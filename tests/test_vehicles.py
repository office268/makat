"""איתור רכב לפי מספר רישוי (מצב offline)."""
from app.vehicles import format_plate, lookup, lookup_offline, normalize_plate


def test_normalize_plate_strips_separators():
    assert normalize_plate("12-345-678") == "12345678"
    assert normalize_plate(" 123 45 678 ") == "12345678"
    assert normalize_plate("") == ""


def test_format_plate_by_length():
    assert format_plate("12345678") == "123-45-678"
    assert format_plate("1234567") == "12-345-67"


def test_offline_lookup_returns_normalized_vehicle():
    vehicle = lookup_offline("12-345-678")
    assert vehicle["make"].startswith("טויוטה")
    assert vehicle["model"] == "COROLLA"
    assert vehicle["year"] == 2016
    assert vehicle["engine_code"] == "1ZR-FE"
    assert vehicle["source"] == "offline"


def test_lookup_falls_back_to_offline_without_network():
    # data.gov.il לא נגיש בסביבת הבדיקה - הנפילה לקובץ המקומי חייבת לעבוד
    assert lookup("12345678") is not None


def test_unknown_plate_returns_none():
    assert lookup_offline("00000000") is None



# --------------------------------------------------------------------------
# למה מספר רישוי אמיתי החזיר "לא נמצא"
# --------------------------------------------------------------------------
import json as _json  # noqa: E402

import pytest  # noqa: E402

from app import vehicles  # noqa: E402

REAL_ROW = {
    "mispar_rechev": 10732802,
    "tozeret_nm": "טויוטה יפן",
    "kinuy_mishari": "COROLLA",
    "degem_nm": "ZRE172L",
    "shnat_yitzur": "2016",
    "degem_manoa": "1ZR-FE",
    "misgeret": "JTDBR32E560095678",
}


class FakeCkan:
    """מאגר מזויף: עונה רק לשאילתה שהוגדרה לו, ורושם מה נשאל."""

    def __init__(self, answers=None, error=None):
        self.answers = answers or {}
        self.error = error
        self.asked = []

    def __call__(self, resource_id, params):
        self.asked.append((resource_id, params))
        if self.error:
            return None, self.error
        key = "q" if "q" in params else params.get("filters")
        return self.answers.get(key, []), None


def test_the_numeric_filter_is_tried_first(monkeypatch):
    """זה הבאג שהחזיר "לא נמצא" לכל רישוי אמיתי.

    ``mispar_rechev`` הוא עמודה מספרית ב-CKAN. סינון עם מחרוזת מחזיר
    אפס שורות בלי שגיאה, כך שרכבי הדוגמה המקומיים עבדו והמציאות לא.
    """
    fake = FakeCkan()
    monkeypatch.setattr(vehicles, "_query", fake)
    vehicles.lookup_detail("107-32-802")

    first_params = fake.asked[0][1]
    assert _json.loads(first_params["filters"]) == {"mispar_rechev": 10732802}
    assert isinstance(_json.loads(first_params["filters"])["mispar_rechev"], int)


def test_a_numeric_match_returns_the_vehicle(monkeypatch):
    numeric = _json.dumps({"mispar_rechev": 10732802})
    monkeypatch.setattr(vehicles, "_query", FakeCkan({numeric: [REAL_ROW]}))
    found = vehicles.lookup_detail("107-32-802")
    assert found["status"] == "found"
    assert found["vehicle"]["model"] == "COROLLA"
    assert found["vehicle"]["vin"] == "JTDBR32E560095678"
    assert found["vehicle"]["source"] == "data.gov.il"


def test_a_text_column_is_still_found(monkeypatch):
    """מאגר אחר באותו פורמט עשוי לשמור את המספר כטקסט."""
    textual = _json.dumps({"mispar_rechev": "10732802"})
    fake = FakeCkan({textual: [REAL_ROW]})
    monkeypatch.setattr(vehicles, "_query", fake)
    assert vehicles.lookup_detail("10732802")["status"] == "found"
    # והמדויקת נוסתה קודם
    assert len(fake.asked) == 2


def test_free_text_catches_what_the_filters_missed(monkeypatch):
    fake = FakeCkan({"q": [REAL_ROW]})
    monkeypatch.setattr(vehicles, "_query", fake)
    assert vehicles.lookup_detail("10732802")["status"] == "found"


def test_the_registry_answering_nothing_is_not_found(monkeypatch):
    monkeypatch.setattr(vehicles, "_query", FakeCkan())
    found = vehicles.lookup_detail("10732802")
    assert found["status"] == "not_found"
    assert len(found["attempts"]) == 3
    assert all(a["error"] is None for a in found["attempts"])


def test_the_registry_being_down_is_not_the_same_as_not_found(monkeypatch):
    """ההבחנה שבלעדיה המסך שולח את המשתמש לבדוק מספר תקין."""
    monkeypatch.setattr(vehicles, "_query", FakeCkan(error="אין גישה למאגר: 403"))
    found = vehicles.lookup_detail("10732802")
    assert found["status"] == "unreachable"
    assert "403" in found["error"]
    assert found["vehicle"] is None


def test_an_empty_plate_is_refused_without_asking_the_registry(monkeypatch):
    fake = FakeCkan()
    monkeypatch.setattr(vehicles, "_query", fake)
    assert vehicles.lookup_detail("")["status"] == "not_found"
    assert fake.asked == []


def test_extra_registries_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("GOV_VEHICLE_RESOURCES", "aaa:ירדו מהכביש, bbb:דו-גלגלי")
    assert vehicles.resources() == [("aaa", "ירדו מהכביש"), ("bbb", "דו-גלגלי")]
    monkeypatch.setenv("GOV_VEHICLE_RESOURCES", "")
    assert vehicles.resources()[0][0] == vehicles.RESOURCE_ID


def test_every_configured_registry_is_asked(monkeypatch):
    monkeypatch.setenv("GOV_VEHICLE_RESOURCES", "aaa:ראשון, bbb:שני")
    fake = FakeCkan()
    monkeypatch.setattr(vehicles, "_query", fake)
    vehicles.lookup_detail("10732802")
    assert {resource for resource, _ in fake.asked} == {"aaa", "bbb"}


def test_lookup_still_falls_back_to_the_sample_file(monkeypatch):
    """התאימות לאחור: החתימה הישנה ממשיכה להתנהג כמו קודם."""
    monkeypatch.setattr(vehicles, "_query", FakeCkan(error="אין רשת"))
    assert vehicles.lookup("12345678")["source"] == "offline"
    assert vehicles.lookup("00000000") is None


def test_the_screen_says_the_registry_is_down_and_not_that_the_plate_is_wrong(
    client, monkeypatch
):
    monkeypatch.setattr(vehicles, "_query", FakeCkan(error="אין גישה למאגר: 403"))
    body = client.post(
        "/", data={"plate": "10732802", "action": "vehicle"}
    ).get_data(as_text=True)
    assert "אינו נגיש כרגע" in body
    assert "נסה שוב" in body


def test_the_screen_explains_where_else_the_vehicle_could_be(client, monkeypatch):
    monkeypatch.setattr(vehicles, "_query", FakeCkan())
    body = client.post(
        "/", data={"plate": "10732802", "action": "vehicle"}
    ).get_data(as_text=True)
    assert "לא נמצא רכב" in body
    assert "ירד מהכביש" in body


def test_the_api_separates_down_from_missing(client, monkeypatch):
    monkeypatch.setattr(vehicles, "_query", FakeCkan(error="אין רשת"))
    assert client.get("/api/vehicle/10732802").status_code == 503
    monkeypatch.setattr(vehicles, "_query", FakeCkan())
    assert client.get("/api/vehicle/10732802").status_code == 404


def test_the_api_can_show_what_it_tried(client, monkeypatch):
    """הדרך לענות על "למה זה אומר לא נמצא" בפרודקשן, בלי לנחש."""
    monkeypatch.setattr(vehicles, "_query", FakeCkan())
    payload = client.get("/api/vehicle/10732802?debug=1").get_json()
    assert payload["status"] == "not_found"
    assert len(payload["attempts"]) == 3
    assert payload["attempts"][0]["strategy"] == "סינון מספרי"
