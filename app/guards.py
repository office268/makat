"""נעילת כתיבה זמנית - עד שמנגנון ההרשאות המלא ייכנס.

האפליקציה עלתה לאוויר בלי שום מנגנון הרשאות, כך שכל מי שיש לו את
הכתובת יכול למחוק מק"טים, לשנות מחירים או לדרוס את הקטלוג בייבוא CSV.
עד שמערכת המשתמשים תהיה מוכנה, נקודות הקצה שמשנות נתונים חסומות
בפרודקשן. חיפוש, צפייה, ייצוא וזרימת הזיהוי ממשיכים לעבוד כרגיל.

לביטול הנעילה (למשל בפיתוח מקומי): READ_ONLY=0
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
        "admin.discovery_start",
        "admin.discovery_step",
        "admin.discovery_cancel",
        "admin.discovery_verify",
        "admin.discovery_delete",
    }
)

MESSAGE = (
    "המערכת נמצאת כרגע במצב קריאה בלבד. שינוי נתונים ייפתח "
    "עם הפעלת מערכת המשתמשים וההרשאות."
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
