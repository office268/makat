"""איתור רכב לפי מספר רישוי, מול המאגר הפתוח של משרד התחבורה (data.gov.il).

המאגר חינמי ולא דורש מפתח. אם אין גישה לרשת (סביבה סגורה, נפילת שירות),
המודול נופל אוטומטית לקובץ רכבי דוגמה מקומי כדי שהזיהוי ימשיך לעבוד.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CKAN_URL = "https://data.gov.il/api/3/action/datastore_search"

# מאגר "כלי רכב פרטיים ומסחריים" של משרד התחבורה
RESOURCE_ID = os.environ.get(
    "GOV_VEHICLES_RESOURCE_ID", "053cea08-09bc-40ec-8f7a-156f0677aff3"
)
TIMEOUT = float(os.environ.get("GOV_API_TIMEOUT", "8"))

SAMPLE_PATH = Path(__file__).resolve().parent.parent / "data" / "vehicles_sample.json"

_sample_cache = None


def normalize_plate(plate):
    """'12-345-67' -> '1234567'."""
    return "".join(ch for ch in str(plate or "") if ch.isdigit())


def format_plate(plate):
    """מעצב מספר רישוי לתצוגה: 8 ספרות -> 123-45-678, 7 ספרות -> 12-345-67."""
    digits = normalize_plate(plate)
    if len(digits) == 8:
        return f"{digits[:3]}-{digits[3:5]}-{digits[5:]}"
    if len(digits) == 7:
        return f"{digits[:2]}-{digits[2:5]}-{digits[5:]}"
    return digits


def _load_samples():
    global _sample_cache
    if _sample_cache is None:
        try:
            _sample_cache = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _sample_cache = []
    return _sample_cache


def _normalize_record(row, source):
    """ממיר רשומה גולמית של משרד התחבורה למבנה אחיד."""
    year = row.get("shnat_yitzur")
    try:
        year = int(year)
    except (TypeError, ValueError):
        year = None
    return {
        "plate": normalize_plate(row.get("mispar_rechev")),
        "plate_display": format_plate(row.get("mispar_rechev")),
        "make": (row.get("tozeret_nm") or "").strip(),
        "model": (row.get("kinuy_mishari") or row.get("degem_nm") or "").strip(),
        "model_code": (row.get("degem_nm") or "").strip(),
        "trim": (row.get("ramat_gimur") or "").strip(),
        "year": year,
        "engine_code": (row.get("degem_manoa") or "").strip(),
        "fuel": (row.get("sug_delek_nm") or "").strip(),
        "color": (row.get("tzeva_rechev") or "").strip(),
        "vin": (row.get("misgeret") or "").strip(),
        "test_valid_until": (row.get("tokef_dt") or "").strip(),
        # שדות נוספים שהמאגר מחזיק. מוצגים רק כשהם מלאים, ולכן שדה
        # שלא קיים ברשומה פשוט לא מופיע במקום להציג שורה ריקה.
        "ownership": (row.get("baalut") or "").strip(),
        "first_on_road": (row.get("moed_aliya_lakvish") or "").strip(),
        "last_test": (row.get("mivchan_acharon_dt") or "").strip(),
        "tyre_front": (row.get("zmig_kidmi") or "").strip(),
        "tyre_rear": (row.get("zmig_ahori") or "").strip(),
        "pollution_group": str(row.get("kvutzat_zihum") or "").strip(),
        "safety_level": str(row.get("ramat_eivzur_betihuty") or "").strip(),
        "source": source,
    }


def _short_make(make):
    """'טויוטה' / 'TOYOTA' -> המילה הראשונה, להצלבה מול טבלת ההתאמות."""
    return (make or "").strip().split()[0] if make else ""


def lookup_offline(plate):
    """חיפוש בקובץ הדוגמאות המקומי."""
    digits = normalize_plate(plate)
    for row in _load_samples():
        if normalize_plate(row.get("mispar_rechev")) == digits:
            return _normalize_record(row, "offline")
    return None


def lookup_online(plate):
    """שאילתה חיה למאגר משרד התחבורה. מחזיר None אם אין תוצאה או אין רשת."""
    digits = normalize_plate(plate)
    if not digits:
        return None
    params = urllib.parse.urlencode(
        {
            "resource_id": RESOURCE_ID,
            "filters": json.dumps({"mispar_rechev": digits}),
            "limit": 1,
        }
    )
    request = urllib.request.Request(
        f"{CKAN_URL}?{params}", headers={"User-Agent": "makat/1.0"}
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None
    records = (payload.get("result") or {}).get("records") or []
    if not records:
        return None
    return _normalize_record(records[0], "data.gov.il")


def lookup(plate, allow_offline=True):
    """מאתר רכב לפי מספר רישוי: קודם המאגר החי, ואם אין - קובץ הדוגמאות."""
    vehicle = lookup_online(plate)
    if vehicle:
        return vehicle
    if allow_offline:
        return lookup_offline(plate)
    return None

