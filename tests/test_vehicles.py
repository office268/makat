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


# --------------------------------------------------------------------------
# איתור המאגרים עצמם, במקום לנחש מזהים
# --------------------------------------------------------------------------

PACKAGES = {
    "results": [
        {
            "title": "כלי רכב פרטיים ומסחריים",
            "resources": [
                {"id": "active-1", "name": "רכב פעיל", "datastore_active": True},
                {"id": "readme", "name": "הסבר", "datastore_active": False},
            ],
        },
        {
            "title": "כלי רכב שהורדו מהכביש",
            "resources": [
                {"id": "offroad-1", "name": "ירדו מהכביש", "datastore_active": True},
            ],
        },
        {
            "title": "תחנות דלק",
            "resources": [
                {"id": "fuel-1", "name": "תחנות", "datastore_active": True},
            ],
        },
    ]
}

FIELDS = {
    "active-1": ["_id", "mispar_rechev", "tozeret_nm"],
    "offroad-1": ["_id", "mispar_rechev", "bitul_dt"],
    "fuel-1": ["_id", "shem_tachana"],   # אין מספרי רישוי - לא רלוונטי
}


def _fake_ckan(monkeypatch, packages=PACKAGES, error=None):
    def ckan(action, params, timeout=None):
        if error:
            return None, error
        if action == "package_search":
            return packages, None
        if action == "datastore_search":
            fields = FIELDS.get(params["resource_id"], [])
            return {"fields": [{"id": name} for name in fields]}, None
        return None, "פעולה לא מוכרת"

    monkeypatch.setattr(vehicles, "_ckan", ckan)


def test_discovery_keeps_only_registries_that_hold_plate_numbers(monkeypatch):
    """הסינון הוא לפי קיום העמודה, לא לפי שם - שמות משתנים."""
    _fake_ckan(monkeypatch)
    found, error = vehicles.discover_resources()
    assert error is None
    assert [resource for resource, _ in found] == ["active-1", "offroad-1"]
    assert "fuel-1" not in [resource for resource, _ in found]


def test_discovery_skips_resources_without_a_datastore(monkeypatch):
    _fake_ckan(monkeypatch)
    found, _ = vehicles.discover_resources()
    assert "readme" not in [resource for resource, _ in found]


def test_discovery_reports_an_unreachable_registry(monkeypatch):
    _fake_ckan(monkeypatch, error="אין גישה למאגר: 403")
    found, error = vehicles.discover_resources()
    assert found == []
    assert "403" in error


def test_lookup_everywhere_finds_the_vehicle_in_another_registry(monkeypatch):
    """הרכב ירד מהכביש. המאגר הרגיל עונה ואין בו כלום - ובאחר יש."""
    _fake_ckan(monkeypatch)
    numeric = _json.dumps({"mispar_rechev": 10732802})

    def query(resource_id, params):
        if resource_id == "offroad-1" and params.get("filters") == numeric:
            return [REAL_ROW], None
        return [], None

    monkeypatch.setattr(vehicles, "_query", query)
    found = vehicles.lookup_everywhere("107-32-802")

    assert found["status"] == "found"
    assert found["found_in"]["resource"] == "offroad-1"
    assert found["vehicle"]["model"] == "COROLLA"
    # והרשימה שמוחזרת היא בדיוק מה שצריך להיכנס להגדרה
    labels = {entry["resource"]: entry["label"] for entry in found["discovered"]}
    assert labels["offroad-1"] == "ירדו מהכביש"


def test_lookup_everywhere_does_not_re_ask_a_registry_already_configured(monkeypatch):
    _fake_ckan(monkeypatch)
    monkeypatch.setenv("GOV_VEHICLE_RESOURCES", "active-1:רכב פעיל")
    asked = []

    def query(resource_id, params):
        asked.append(resource_id)
        return [], None

    monkeypatch.setattr(vehicles, "_query", query)
    found = vehicles.lookup_everywhere("10732802")

    assert found["status"] == "not_found"
    # active-1 נשאל שלוש פעמים במסלול הרגיל, ולא שוב בסריקה
    assert asked.count("active-1") == 3
    assert asked.count("offroad-1") == 3


def test_lookup_everywhere_returns_early_when_the_normal_path_found_it(monkeypatch):
    numeric = _json.dumps({"mispar_rechev": 10732802})
    monkeypatch.setattr(vehicles, "_query", FakeCkan({numeric: [REAL_ROW]}))
    called = []
    monkeypatch.setattr(
        vehicles, "discover_resources", lambda *a, **k: (called.append(1), ([], None))[1]
    )
    assert vehicles.lookup_everywhere("10732802")["status"] == "found"
    assert called == []   # סריקה יקרה לא רצה בכלל כשלא צריך אותה


def test_the_api_can_scan_every_registry_on_request(client, monkeypatch):
    _fake_ckan(monkeypatch)
    numeric = _json.dumps({"mispar_rechev": 10732802})

    def query(resource_id, params):
        if resource_id == "offroad-1" and params.get("filters") == numeric:
            return [REAL_ROW], None
        return [], None

    monkeypatch.setattr(vehicles, "_query", query)
    payload = client.get("/api/vehicle/10732802?discover=1").get_json()
    assert payload["status"] == "found"
    assert payload["found_in"]["resource"] == "offroad-1"
    assert payload["vehicle"]["model"] == "COROLLA"


def test_the_scan_is_not_run_on_the_normal_request(client, monkeypatch):
    """סריקה של עשרות מאגרים אינה משהו שקורה בכל לחיצה על "זהה רכב"."""
    monkeypatch.setattr(vehicles, "_query", FakeCkan())
    monkeypatch.setattr(
        vehicles, "discover_resources",
        lambda *a, **k: pytest.fail("סריקה רצה בבקשה רגילה"),
    )
    client.post("/", data={"plate": "10732802", "action": "vehicle"})
    client.get("/api/vehicle/10732802?debug=1")
