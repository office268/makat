"""איתור רכב לפי מספר רישוי, מול המאגר הפתוח של משרד התחבורה (data.gov.il).

המאגר חינמי ולא דורש מפתח. אם אין גישה לרשת (סביבה סגורה, נפילת שירות),
המודול נופל אוטומטית לקובץ רכבי דוגמה מקומי כדי שהזיהוי ימשיך לעבוד.

**שתי מלכודות שנצרבו כאן, כי שתיהן נראות אותו דבר על המסך.**

הראשונה: ``mispar_rechev`` הוא עמודה *מספרית* ב-CKAN. סינון עם המחרוזת
``"10732802"`` מול עמודה מספרית פשוט לא מתאים, והמאגר מחזיר אפס שורות
בלי שגיאה. התוצאה היא "לא נמצא רכב" לכל מספר רישוי אמיתי, בזמן שרכבי
הדוגמה המקומיים ממשיכים לעבוד - כשל שנראה כמו מאגר ריק ואינו כזה.
לכן יש כאן כמה אסטרטגיות סינון, ולא אחת.

השנייה: "המאגר לא ענה" ו-"אין רכב כזה" הן שתי תשובות שונות לגמרי -
אחת אומרת לנסות שוב, השנייה אומרת שהמספר שגוי. קודם שתיהן חזרו כ-
``None``, והמסך אמר "לא נמצא רכב" גם כשהמאגר כלל לא נשאל. לכן
``lookup_detail`` מחזיר גם *למה*, והמסך אומר את האמת.
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


def resources():
    """המאגרים שנשאלים, לפי הסדר. "מזהה:תווית", מופרדים בפסיק.

    רכב שירד מהכביש, דו-גלגלי ורכב כבד יושבים במאגרים נפרדים מזה של
    הרכב הפרטי והמסחרי. הוספת מאגר היא שינוי הגדרה ולא פריסה, כי
    המזהים משתנים מעת לעת ואינם דבר שראוי לקבע בקוד.
    """
    raw = os.environ.get("GOV_VEHICLE_RESOURCES", "").strip()
    if not raw:
        return [(RESOURCE_ID, "רכב פרטי ומסחרי")]
    found = []
    for chunk in raw.split(","):
        resource_id, _, label = chunk.strip().partition(":")
        if resource_id.strip():
            found.append((resource_id.strip(), label.strip() or resource_id.strip()))
    return found or [(RESOURCE_ID, "רכב פרטי ומסחרי")]


def _query(resource_id, params):
    """קריאה אחת ל-CKAN. מחזיר (רשומות, שגיאה)."""
    query = urllib.parse.urlencode({"resource_id": resource_id, "limit": 1, **params})
    request = urllib.request.Request(
        f"{CKAN_URL}?{query}", headers={"User-Agent": "makat/1.0"}
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return None, f"אין גישה למאגר: {exc}"
    except ValueError as exc:
        return None, f"תשובה שאינה JSON: {exc}"
    if not payload.get("success", True):
        return None, str(payload.get("error") or "המאגר החזיר שגיאה")
    return (payload.get("result") or {}).get("records") or [], None


def _strategies(digits):
    """דרכי החיפוש, מהמדויקת לסלחנית.

    הסינון המספרי הוא הנכון - זה טיפוס העמודה - ובכל זאת הוא לא
    ראשון-ויחיד: מאגר אחר באותו פורמט עשוי לשמור את המספר כטקסט,
    וחיפוש חופשי תופס גם את זה. הראשון שמחזיר שורה מנצח.
    """
    return [
        ("סינון מספרי", {"filters": json.dumps({"mispar_rechev": int(digits)})}),
        ("סינון טקסטואלי", {"filters": json.dumps({"mispar_rechev": digits})}),
        ("חיפוש חופשי", {"q": digits}),
    ]


def lookup_detail(plate):
    """מאתר רכב, ומחזיר גם *מה קרה*.

    ``status``: ``found`` / ``not_found`` / ``unreachable``.
    ``attempts``: מה נוסה ומה חזר - זה מה שהופך "לא נמצא" מניחוש
    לאבחנה, ואת המסך מ"אין רכב כזה" ל"המאגר לא ענה" כשזה המצב.
    """
    digits = normalize_plate(plate)
    result = {"vehicle": None, "status": "not_found", "attempts": [], "error": None}
    if not digits:
        result["error"] = "מספר רישוי ריק"
        return result

    reached = False
    for resource_id, label in resources():
        for name, params in _strategies(digits):
            records, error = _query(resource_id, params)
            result["attempts"].append(
                {
                    "resource": label,
                    "strategy": name,
                    "error": error,
                    "records": None if records is None else len(records),
                }
            )
            if error is not None:
                continue
            reached = True
            if records:
                result["vehicle"] = _normalize_record(records[0], "data.gov.il")
                result["status"] = "found"
                return result

    if not reached:
        result["status"] = "unreachable"
        result["error"] = next(
            (a["error"] for a in result["attempts"] if a["error"]), "המאגר לא נגיש"
        )
    return result


def lookup_online(plate):
    """שאילתה חיה למאגר משרד התחבורה. מחזיר None אם אין תוצאה או אין רשת."""
    return lookup_detail(plate)["vehicle"]


def lookup(plate, allow_offline=True):
    """מאתר רכב לפי מספר רישוי: קודם המאגר החי, ואם אין - קובץ הדוגמאות."""
    vehicle = lookup_online(plate)
    if vehicle:
        return vehicle
    if allow_offline:
        return lookup_offline(plate)
    return None

