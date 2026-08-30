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

