"""מתג חירום: חוסם כל שינוי נתונים, גם למי שההרשאות מתירות לו.

**כבוי כברירת מחדל, ובכוונה.** הוא נולד כפתרון זמני: האפליקציה עלתה
לאוויר בלי שום מנגנון הרשאות, וכל מי שידע את הכתובת יכול היה למחוק
מק"טים או לדרוס את הקטלוג בייבוא CSV. זה כבר לא המצב - ``role_required``
שומר על כל נקודת קצה שמשנה נתונים, ומכונאי נחסם בכולן.

מה שנשאר לו הוא תפקיד צר יותר ואמיתי: להקפיא כתיבה כשמשהו השתבש -
חשד לדליפה, מיגרציה שהתפקששה, ייבוא שהכניס זבל ורוצים להבין מה קרה
לפני שממשיכים. חיפוש, צפייה, ייצוא וזרימת הזיהוי ממשיכים לעבוד.

    READ_ONLY=1     הקפאה
    (ברירת מחדל)    פתוח, וההרשאות עושות את העבודה

**הרשימה שלמטה ידנית, וזו מגבלה שצריך להכיר.** נקודת קצה חדשה שמשנה
נתונים אינה נחסמת עד שמישהו יוסיף אותה לכאן; ``admin.columns_save``
ופעולות ``/team`` אינן בה. זה נסבל כל עוד המתג הוא חירום ולא ברירת
מחדל - ההגנה היומיומית היא ההרשאות, לא הרשימה הזאת.
"""
from flask import jsonify, redirect, request, url_for
from flask import flash

# נקודות קצה שמשנות נתונים. כל השאר נשאר פתוח לקריאה.
MUTATING_ENDPOINTS = frozenset(
    {
        "web.part_create",
        "web.part_edit",
        "web.part_delete",
        "web.import_csv",
        "api.create_part",
        "api.update_part",
        "api.delete_part",
        "admin.vehicle_import_start",
        "admin.vehicle_import_step",
        "admin.vehicle_import_cancel",
        "admin.fleet_stats_start",
        "admin.fleet_stats_step",
        "admin.fleet_stats_cancel",
        "admin.discovery_start",
        "admin.discovery_step",
        "admin.discovery_cancel",
        "admin.discovery_verify",
        "admin.discovery_delete",
    }
)

# מה שאינו כאן ובכוונה: השליפה החיה (identify.lookup_*). היא חלק
# מזרימת הזיהוי, וחסימה שלה הייתה מכבה את המסך במקום להגן על הקטלוג.
# הכתיבה לקטלוג שבסופה נבדקת מול READ_ONLY בתוך live_lookup עצמו -
# השליפה רצה, התוצאה מוצגת, והשמירה נדחית.

MESSAGE = (
    "המערכת נמצאת כרגע במצב קריאה בלבד. זהו מתג חירום שהופעל "
    "בשרת - פנה למנהל המערכת."
)


def _wants_json(request):
    """בקשות ה-API וקריאות ה-fetch מהדפדפן צריכות JSON, לא הפניה לדף."""
    return (
        request.path.startswith("/api/")
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
    )


def register_read_only_guard(app):
    """חוסם שינוי נתונים כשהאפליקציה במצב קריאה בלבד."""

    @app.before_request
    def _block_mutations():
        if not app.config.get("READ_ONLY"):
            return None
        if request.endpoint not in MUTATING_ENDPOINTS:
            return None
        # GET על טופס עריכה הוא צפייה בלבד - חוסמים רק את השליחה עצמה
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return None

        app.logger.warning(
            "נחסמה בקשת שינוי במצב קריאה בלבד: %s %s", request.method, request.path
        )
        if _wants_json(request):
            return jsonify({"error": MESSAGE, "read_only": True}), 403
        flash(MESSAGE, "warning")
        return redirect(url_for("web.parts_list"))
