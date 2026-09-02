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
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CKAN_URL = "https://data.gov.il/api/3/action/datastore_search"

# מאגר "כלי רכב פרטיים ומסחריים" של משרד התחבורה
RESOURCE_ID = os.environ.get(
    "GOV_VEHICLES_RESOURCE_ID", "053cea08-09bc-40ec-8f7a-156f0677aff3"
)

# המאגרים שנשאלים כברירת מחדל, לפי הסדר.
#
# הראשון הוא זה שהיה כאן מאז ומתמיד - וזו הייתה הבעיה: המרשם של משרד
# התחבורה **מפוצל לשני משאבים**, "מספרי רישוי של כלי רכב פרטיים
# ומסחריים" ו-"...המשך". רכב שיושב בחלק השני החזיר "לא נמצא" למרות
# שהמאגר ענה כראוי - הוא פשוט נשאל על חצי מהמרשם.
#
# השלישי הוא הרכבים שירדו מהכביש. רכב שבוטל אינו במרשם הפעיל, ומכונאי
# שמחזיק אותו בחצר עדיין צריך לו חלפים.
DEFAULT_RESOURCES = [
    (RESOURCE_ID, "רכב פרטי ומסחרי"),
    ("0866573c-40cd-4ca8-91d2-9dd2d7a492e5", "רכב פרטי ומסחרי · המשך"),
    ("851ecab1-0622-4dbe-a6c7-f950cf82abf9", "ירדו מהכביש · ביטול סופי"),
]

TIMEOUT = float(os.environ.get("GOV_API_TIMEOUT", "6"))
# תקציב זמן לכל החיפוש. מספר מאגרים כפול מספר אסטרטגיות זה הרבה
# בקשות, ו-gunicorn הורג בקשה אחרי 60 שניות. עדיף להפסיק ולומר
# "המאגר איטי" מאשר להיחתך באמצע בלי הודעה.
LOOKUP_BUDGET = float(os.environ.get("GOV_LOOKUP_BUDGET", "20"))

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


# שמות אפשריים לעמודת מספר השלדה. המרשם פורסם בעבר עם ``misgeret``,
# ושם של עמודה במאגר פתוח אינו חוזה - הוא משתנה בין גרסאות ובין
# משאבים. קריאה לפי רשימה עולה כלום ומצילה את השדה שכל התהליך תלוי בו.
VIN_FIELDS = ("misgeret", "mispar_shilda", "shilda", "vin", "mispar_misgeret")


def _first(row, *names):
    """הערך הראשון שקיים ואינו ריק, מבין כמה שמות עמודה אפשריים."""
    for name in names:
        value = row.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


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
        "vin": _first(row, *VIN_FIELDS),
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
        return list(DEFAULT_RESOURCES)
    found = []
    for chunk in raw.split(","):
        resource_id, _, label = chunk.strip().partition(":")
        if resource_id.strip():
            found.append((resource_id.strip(), label.strip() or resource_id.strip()))
    return found or list(DEFAULT_RESOURCES)


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


def by_model(make, model, resource_id=None):
    """רכב אמיתי אחד מהדגם הזה, או None.

    זריעת הקטלוג צריכה *שלדה*, כי חיפוש קטלוג היצרן הוא לפי שלדה -
    ודגם אינו שלדה. המרשם הוא המקום היחיד שבו יש שלדות אמיתיות, והוא
    יודע לסנן לפי יצרן ודגם בדיוק כמו שהוא מסנן לפי מספר רישוי.

    ‏``limit`` הוא 1 ב-``_query``, ולכן זה רכב שרירותי מהדגם ולא
    "הרכב הנכון" - וזה בסדר: כל רכב מהדגם נותן את אותה שלדה מבחינת
    שמונת התווים שקובעים את ההתאמה. שנת הדגם עשויה להשתנות, ולכן
    השדות המוחזרים הם של הרכב שנמצא ולא של הדגם בכללותו.
    """
    make, model = (make or "").strip(), (model or "").strip()
    if not model:
        return None
    targets = [(resource_id, "")] if resource_id else resources()
    # היצרן במרשם הוא "טויוטה יפן"; סינון על "טויוטה" לא יתפוס. לכן
    # מנסים קודם עם היצרן ואחריו בלעדיו - הדגם לבדו כבר מצמצם מספיק.
    filters = [{"kinuy_mishari": model}]
    if make:
        filters.insert(0, {"tozeret_nm": make, "kinuy_mishari": model})
    for target_id, _label in targets:
        for where in filters:
            records, error = _query(target_id, {"filters": json.dumps(where)})
            if error or not records:
                continue
            found = _normalize_record(records[0], "data.gov.il")
            if (found.get("vin") or "").strip():
                return found
    return None


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


# --------------------------------------------------------------------------
# איתור המאגרים עצמם
# --------------------------------------------------------------------------

PACKAGE_SEARCH = CKAN_URL.replace("datastore_search", "package_search")
# מה מחפשים כשמאתרים מאגרי רכב. ברירת המחדל תופסת את המאגרים
# הרלוונטיים בלי לתלות את עצמנו בשם מדויק של חבילה.
DISCOVER_QUERY = os.environ.get("GOV_DISCOVER_QUERY", "כלי רכב")
DISCOVER_ROWS = int(os.environ.get("GOV_DISCOVER_ROWS", 30))
# השדה שמסמן "כאן יש מספרי רישוי". מאגר שאין בו אותו אינו רלוונטי.
PLATE_FIELD = "mispar_rechev"


def _ckan(action, params, timeout=None):
    """קריאה ל-CKAN שאינה datastore_search. מחזיר (result, שגיאה)."""
    url = f"{CKAN_URL.rsplit('/', 1)[0]}/{action}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": "makat/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout or TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return None, f"אין גישה למאגר: {exc}"
    except ValueError as exc:
        return None, f"תשובה שאינה JSON: {exc}"
    if not payload.get("success", True):
        return None, str(payload.get("error") or "המאגר החזיר שגיאה")
    return payload.get("result"), None


def resource_fields(resource_id):
    """שמות העמודות של מאגר, בלי למשוך ממנו שורות."""
    result, error = _ckan("datastore_search", {"resource_id": resource_id, "limit": 0})
    if error or not result:
        return [], error
    return [field.get("id") for field in result.get("fields") or []], None


def discover_resources(query=None, rows=None):
    """מאתר ב-CKAN את כל המאגרים שיש בהם מספרי רישוי.

    למה זה קיים: רכב שירד מהכביש, דו-גלגלי ורכב כבד יושבים במאגרים
    נפרדים, והמזהים שלהם משתנים מעת לעת. לנחש UUID ולקבע אותו בקוד זה
    לכתוב באג עתידי; לשאול את CKAN מי המאגרים ולסנן לפי קיום העמודה
    ``mispar_rechev`` זה לקבל את התשובה הנכונה גם בעוד שנה.

    מחזיר (רשימת (מזהה, תווית), שגיאה).
    """
    result, error = _ckan(
        "package_search",
        {"q": query or DISCOVER_QUERY, "rows": rows or DISCOVER_ROWS},
    )
    if error:
        return [], error

    found, seen = [], set()
    for package in (result or {}).get("results") or []:
        for resource in package.get("resources") or []:
            resource_id = resource.get("id")
            if not resource_id or resource_id in seen:
                continue
            if not resource.get("datastore_active"):
                continue
            seen.add(resource_id)
            fields, _ = resource_fields(resource_id)
            if PLATE_FIELD in fields:
                label = (
                    resource.get("name")
                    or package.get("title")
                    or package.get("name")
                    or resource_id
                )
                found.append((resource_id, str(label).strip()))
    return found, None


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
    deadline = time.monotonic() + LOOKUP_BUDGET
    for resource_id, label in resources():
        for name, params in _strategies(digits):
            if time.monotonic() > deadline:
                result["attempts"].append(
                    {"resource": label, "strategy": name,
                     "error": "נגמר תקציב הזמן", "records": None}
                )
                result["status"] = "unreachable" if not reached else "not_found"
                result["error"] = result["error"] or (
                    f"החיפוש עבר {LOOKUP_BUDGET:.0f} שניות ונעצר."
                )
                return result
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
            # חיפוש חופשי מחזיר כל שורה שהמספר מופיע בה, ולא בהכרח
            # בעמודת הרישוי. רכב שגוי כאן אינו אי-דיוק - הוא חלפים
            # שלא נכנסים לרכב, ולכן ההתאמה נבדקת לפני שהיא מתקבלת.
            records = [
                row for row in records
                if normalize_plate(row.get("mispar_rechev")) == digits
            ]
            if records:
                result["vehicle"] = _normalize_record(records[0], "data.gov.il")
                result["status"] = "found"
                result["found_in"] = {"resource": resource_id, "label": label}
                # השורה כפי שהמאגר החזיר אותה. זה מה שמבדיל בין "העמודה
                # לא קיימת" לבין "העמודה קיימת וריקה לרכב הזה" - שתי
                # בעיות שונות שנראות זהות במסך.
                result["raw"] = records[0]
                return result

    if not reached:
        result["status"] = "unreachable"
        result["error"] = next(
            (a["error"] for a in result["attempts"] if a["error"]), "המאגר לא נגיש"
        )
    return result


def lookup_everywhere(plate):
    """כמו ``lookup_detail``, אבל אחרי כישלון גם מחפש בכל מאגר שיש בו רישוי.

    יקר - זו סריקה של עשרות מאגרים - ולכן אינו מסלול הבקשה הרגיל אלא
    כלי אבחון. הפלט שלו הוא בדיוק מה שצריך להיכנס ל-
    ``GOV_VEHICLE_RESOURCES`` כדי שהמסלול הרגיל ימצא את הרכב מיד.
    """
    result = lookup_detail(plate)
    if result["status"] == "found":
        return result

    digits = normalize_plate(plate)
    if not digits:
        return result

    known = {resource_id for resource_id, _ in resources()}
    discovered, error = discover_resources()
    result["discovered"] = [
        {"resource": resource_id, "label": label, "known": resource_id in known}
        for resource_id, label in discovered
    ]
    if error:
        result["error"] = result["error"] or error
        return result

    for resource_id, label in discovered:
        if resource_id in known:
            continue
        for name, params in _strategies(digits):
            records, query_error = _query(resource_id, params)
            result["attempts"].append(
                {
                    "resource": label,
                    "strategy": name,
                    "error": query_error,
                    "records": None if records is None else len(records),
                }
            )
            if query_error is None and records:
                result["vehicle"] = _normalize_record(records[0], "data.gov.il")
                result["status"] = "found"
                result["found_in"] = {"resource": resource_id, "label": label}
                return result
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

