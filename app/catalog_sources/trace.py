"""יומן חקירה לשליפה אחת: מה נמשך, מה נשלח למודל, ומה הוא ענה.

בלי זה, לשלוש תוצאות שונות לגמרי יש אותה שורה על המסך:

  * האתר החזיר דף תוצאות אמיתי, והחלק פשוט לא קיים לרכב הזה.
  * האתר הפנה אותנו לדף נחיתה, והמודל קרא דף שאין בו מק"טים בכלל.
  * האתר החזיר שלד ריק כי התוכן נבנה ב-JavaScript.

כולן נגמרות ב'לא החזיר מק"ט', ואי אפשר לתקן מה שאי אפשר להבדיל בין
מקרים שלו. לכן כל שלב בדרך רושם שורה: הכתובת שנפתחה, הכתובת שאליה
הופנינו בפועל, גודל הדף, כותרתו, כמה נשאר אחרי הצמצום, פתיחת הטקסט
שנשלח למודל, ומה המודל החזיר.

היומן מיועד לאדם שקורא אותו, ולכן הוא טקסט ולא JSON, והוא נאסף לכל
שלב בנפרד (``start`` לפני המקור, ``lines`` אחריו) כדי ששלב לא יירש
את השורות של קודמו.

הבידוד הוא לכל thread, כי זה גבול הבקשה ב-gunicorn עם עובדי threads.
מה שרץ ב-thread אחר (מסלול הדפדפן) לא נרשם - ובכוונה: יומן שמערבב
שתי שליפות גרוע מיומן חסר.
"""
import os
import re
import sys
import threading

# כיבוי מלא בסביבה שלא רוצה את התקורה. ברירת המחדל דלוקה: יומן שכבוי
# ביום שבו צריך אותו הוא יומן שלא קיים.
ENABLED = os.environ.get("CATALOG_TRACE", "1").strip() != "0"
# תקרת שורות לשלב. אין כאן לולאה שמייצרת שורות, אבל יומן בלי תקרה
# הוא זיכרון בלי תקרה.
MAX_LINES = int(os.environ.get("CATALOG_TRACE_LINES", 200))
# כמה מהטקסט שנשלח למודל נכנס ליומן. זו השורה שמבדילה בין דף תוצאות
# לדף נחיתה, ומאתיים תווים מספיקים לה.
PREVIEW_CHARS = int(os.environ.get("CATALOG_TRACE_PREVIEW", 400))
# האם היומן נשפך גם ל-stdout. ‏gunicorn רץ עם accesslog="-", ו-Railway
# אוסף את stdout של המכולה - כך שכל שורה כאן מגיעה ליומן הריצה של
# השירות, ואפשר לקרוא כישלון בלי שמישהו יעתיק אותו מהמסך.
TO_STDOUT = os.environ.get("CATALOG_TRACE_STDOUT", "1").strip() != "0"
# הסימן שמאפשר לשלוף את השורות שלנו מתוך יומן שרובו שורות גישה.
MARKER = "[makat]"

_local = threading.local()


def start():
    """פותח יומן חדש ל-thread הזה. כל מה שנרשם עד כה נזרק."""
    _local.lines = [] if ENABLED else None
    _local.stages = []


# --------------------------------------------------------------------------
# שלבים: התשובה לשאלה "איפה זה נפל", בלי לקרוא ארבעים שורות
# --------------------------------------------------------------------------

def stage(name, ok, detail="", hint=""):
    """שלב אחד בדרך, והאם הוא עבר.

    היומן הוא זרם - הוא אומר *מה קרה*. השלבים הם שלד - הם אומרים
    *איפה נעצר*. שליפה שנכשלת מייצרת שלושים שורות יומן, ומי שמסתכל
    צריך להסיק מהן איזה שלב הרג אותה; רשימת השלבים אומרת את זה
    ישירות, והיא מה שעולה למסך.

    ``hint`` הוא מה לעשות, כשיש מה. בלעדיו נשאר תיאור.
    """
    stages = getattr(_local, "stages", None)
    if stages is None:
        return
    stages.append({
        "name": str(name),
        "ok": bool(ok),
        "detail": str(detail or "").strip(),
        "hint": str(hint or "").strip(),
    })


def stages():
    """השלבים שנרשמו, לפי הסדר."""
    return [dict(row) for row in getattr(_local, "stages", None) or ()]


def verdict():
    """השלב הראשון שנכשל, או None. זו "הסיבה" בשורה אחת."""
    for row in getattr(_local, "stages", None) or ():
        if not row["ok"]:
            return dict(row)
    return None


def active():
    return getattr(_local, "lines", None) is not None


def note(text):
    """שורה אחת ליומן. מתעלמת בשקט כשאין יומן פתוח."""
    lines = getattr(_local, "lines", None)
    if lines is None:
        return
    text = str(text).replace("\r", "").strip()
    if not text:
        return
    if len(lines) < MAX_LINES:
        lines.append(text)
    elif len(lines) == MAX_LINES:
        lines.append("… היומן נקטע")


def lines():
    """מה שנרשם מאז ``start``. רשימה חדשה - הקוראים לא משנים אותנו."""
    return list(getattr(_local, "lines", None) or ())


def clear():
    """סוגר את היומן. אחריו ``note`` הוא no-op."""
    _local.lines = None
    _local.stages = None


# --------------------------------------------------------------------------
# עוזרים לקריאת דף
# --------------------------------------------------------------------------

_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


def page_title(html):
    """כותרת הדף. "Search results for VF3..." לעומת "7zap - Home" היא
    ההבחנה שאנחנו מחפשים, והיא נמצאת שם לפני כל שאר הפענוח."""
    match = _TITLE.search(html or "")
    if not match:
        return ""
    return " ".join(match.group(1).split())[:120]


def preview(text, label="פתיחת הטקסט"):
    """רושם את תחילת הטקסט שנשלח למודל, בשורה אחת ובלי רעש."""
    if not active():
        return
    flat = " ".join(str(text or "").split())
    if not flat:
        note(f"{label}: (ריק)")
        return
    cut = flat[:PREVIEW_CHARS]
    note(f"{label}: {cut}" + ("…" if len(flat) > PREVIEW_CHARS else ""))


# --------------------------------------------------------------------------
# שפיכה ל-stdout: היומן במקום שאפשר לקרוא ממנו מרחוק
# --------------------------------------------------------------------------

# מפתחות שנכנסים לכתובת של שירות הבאה. היומן רושם את כתובת *היעד*
# ולא את הכתובת הבנויה, אבל שורה אחת שתעקוף את זה תדליף מפתח ליומן
# שאפשר לקרוא מהדשבורד, ולכן החיתוך כאן ולא במקום שמייצר את השורה.
_SECRET = re.compile(r"\b(api_?key|token|secret|password)=([^&\s\"']+)", re.I)


def _safe(line):
    return _SECRET.sub(r"\1=***", str(line))


def echo(title, extra=()):
    """שופך את היומן של השלב ל-stdout, כבלוק אחד וממוסגר.

    הבלוק נכתב בכתיבה אחת כדי שלא יתערבב עם עובד אחר שכותב במקביל,
    וכל שורה נושאת את ``MARKER`` כדי שאפשר יהיה לסנן אותה משורות
    הגישה. כישלון בכתיבה נבלע: יומן לא מפיל שליפה.
    """
    if not TO_STDOUT:
        return
    rows = list(lines()) + [str(item) for item in extra if str(item).strip()]
    stop = verdict()
    if stop:
        detail = f" - {stop['detail']}" if stop["detail"] else ""
        rows.append(f"✗ נעצר ב: {stop['name']}{detail}")
        # הרמז הוא מה *לעשות*. בלעדיו הבלוק אומר מה נשבר ולא מה לתקן,
        # ומי שקורא יומן מרחוק הוא בדיוק מי שצריך את החלק השני.
        if stop["hint"]:
            rows.append(f"  ← {stop['hint']}")
    block = [f"{MARKER} ── {_safe(title)}"]
    block += [f"{MARKER} {_safe(row)}" for row in rows]
    block.append(f"{MARKER} ── סוף ({len(rows)} שורות)")
    try:
        sys.stdout.write("\n".join(block) + "\n")
        sys.stdout.flush()
    except Exception:
        pass
