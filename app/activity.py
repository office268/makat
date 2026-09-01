"""לוג שימוש מפורט - מי עשה מה במערכת, מתי, וכמה זמן זה לקח.

הלוג נכתב אוטומטית: לכל בקשה שאינה רעש (קבצים סטטיים, בדיקת בריאות,
פולינג של מסכי הניהול) נרשמת שורה אחת בסוף הבקשה, עם המשתמש, הארגון,
הנתיב, הסטטוס ומשך הטיפול. מסכים שיודעים לספר יותר מזה - התחברות,
חיפוש לפי רכב, ייבוא CSV - מוסיפים פרטים דרך note() באמצע הבקשה,
והם נספחים לאותה שורה. כך יש בדיוק אירוע אחד לכל פעולה של המשתמש.

מה שלא נשמר, בכוונה: ערכי שדות של טפסים. מהטופס נשמרים רק שמות
השדות, כדי שסיסמאות וטוקנים לא ידלפו ללוג. מה שכן חשוב לתעד -
מספר רישוי, מק"ט, מספר תוצאות - נמסר במפורש דרך note().
"""
import json
import time
from datetime import datetime, timedelta, timezone

from flask import g, has_request_context, request
from sqlalchemy import func

from .models import db

# שדות שהערך שלהם לא נכנס ללוג בשום מצב
SENSITIVE_KEYS = frozenset(
    {"password", "password_confirm", "csrf_token", "token", "api_key", "secret"}
)

# נקודות קצה שנרשמות היו מציפות את הלוג בלי לספר כלום: נכסים, בדיקת
# בריאות, והפולינג שמסכי הניהול עושים כל שנייה. ההתחלה והסיום של אותן
# עבודות כן נרשמים - רק המנות שביניהן לא.
SKIP_ENDPOINTS = frozenset(
    {
        "static",
        "healthz",
        "pwa.manifest",
        "pwa.service_worker",
        "pwa.offline",
        "admin.vehicle_import_status",
        "admin.vehicle_import_step",
        "admin.fleet_stats_status",
        "admin.fleet_stats_step",
        "admin.discovery_status",
        "admin.discovery_step",
        "admin.discovery_plan",
    }
)

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

MAX_VALUE_LEN = 200
MAX_ARGS = 20

# שם הפעולה -> תיאור בעברית. שם הפעולה הוא שם נקודת הקצה, אלא אם
# מסך מסוים ביקש שם משלו דרך note() (למשל auth.login_failed).
ACTION_LABELS = {
    "auth.login": "כניסה למערכת",
    "auth.login_failed": "ניסיון כניסה שנכשל",
    "auth.logout": "יציאה מהמערכת",
    "auth.account": "מסך החשבון",
    "identify.index": "מסך הזיהוי",
    "identify.vehicle_lookup": "זיהוי רכב לפי מספר רישוי",
    "identify.part_search": 'חיפוש מק"ט לרכב',
    "identify.legacy_urls": "כתובת ישנה של מסך הזיהוי",
    "lookup.start": "שליפה חיה לפי מספר שלדה",
    "lookup.cache_hit": "שליפה חיה - מתוך תשובה שמורה",
    "identify.lookup_step": "שליפה חיה - שלב",
    "identify.lookup_cancel": "ביטול שליפה חיה",
    "lookup.stopped": "שליפה חיה - נעצרה אחרי מקור",
    "identify.api_vehicle": "שליפת רכב (API)",
    "identify.api_identify": "זיהוי חלק (API)",
    "web.dashboard": "לוח מחוונים",
    "web.parts_list": 'רשימת מק"טים',
    "web.part_detail": 'צפייה בכרטיס מק"ט',
    "web.part_lookup": 'איתור מק"ט מדויק',
    "web.part_create": 'הוספת מק"ט',
    "web.part_edit": 'עריכת מק"ט',
    "web.part_delete": 'מחיקת מק"ט',
    "web.vehicles": "חיפוש חלקים לפי רכב",
    "web.manufacturers": "יצרנים",
    "web.categories": "קטגוריות",
    "web.suppliers": "ספקים",
    "web.export_csv": "ייצוא CSV",
    "web.import_csv": "ייבוא CSV",
    "web.not_found": "דף שלא נמצא",
    "team.index": "ניהול משתמשים",
    "team.add_user": "הוספת מורשה",
    "team.change_role": "שינוי תפקיד",
    "team.toggle_active": "הפעלה/השבתה של משתמש",
    "admin.vehicle_import": "מסך ייבוא קטלוג דגמי רכב",
    "admin.vehicle_import_start": "התחלת ייבוא דגמי רכב",
    "admin.vehicle_import_cancel": "ביטול ייבוא דגמי רכב",
    "admin.discovery": 'מסך גילוי מק"טים',
    "admin.discovery_start": 'התחלת גילוי מק"טים',
    "admin.discovery_cancel": 'ביטול גילוי מק"טים',
    "admin.discovery_review": "סקירת מה שהתגלה",
    "admin.columns": 'מסך עמודות טבלת המק"טים',
    "admin.columns_save": "שינוי עמודות הטבלה",
    "admin.discovery_verify": 'אימות מק"ט מול הרשת',
    "admin.discovery_delete": 'מחיקת מק"טים שנפסלו',
    "api.list_parts": 'רשימת מק"טים (API)',
    "api.get_part": 'מק"ט בודד (API)',
    "api.create_part": 'יצירת מק"ט (API)',
    "api.update_part": 'עדכון מק"ט (API)',
    "api.delete_part": 'מחיקת מק"ט (API)',
    "api.search": "חיפוש (API)",
    "api.stats": "סטטיסטיקות (API)",
    "activity.index": "לוג השימוש",
    "activity.export_csv": "ייצוא לוג השימוש",
    "activity.entry": "פרטי אירוע בלוג",
    "unknown": "כתובת לא מוכרת",
}


def _now():
    return datetime.now(timezone.utc)


def to_local(value, tz_name="Asia/Jerusalem"):
    """זמן ה-UTC שנשמר, בשעון המקומי. בלי מסד אזורי זמן - חוזר כמו שהוא."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    try:
        from zoneinfo import ZoneInfo

        return value.astimezone(ZoneInfo(tz_name))
    except Exception:
        return value


def action_label(action):
    """תיאור בעברית לפעולה. פעולה לא מוכרת מוצגת כמו שהיא."""
    return ACTION_LABELS.get(action, action or "—")


class ActivityLog(db.Model):
    """אירוע שימוש בודד."""

    __tablename__ = "activity_log"

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=_now, nullable=False, index=True)

    organization_id = db.Column(
        db.Integer, db.ForeignKey("organizations.id"), index=True
    )
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    # תצלום של המשתמש בזמן האירוע - הלוג נשאר קריא גם אחרי שהמשתמש נמחק.
    # מה שנרשם הוא הזהות שאיתה נכנס, כלומר מספר הטלפון.
    user_label = db.Column(db.String(190))
    user_role = db.Column(db.String(20))

    action = db.Column(db.String(80), nullable=False, index=True)
    summary = db.Column(db.String(255))
    entity_type = db.Column(db.String(40))
    entity_id = db.Column(db.Integer)

    method = db.Column(db.String(8))
    path = db.Column(db.String(255))
    status_code = db.Column(db.Integer, index=True)
    duration_ms = db.Column(db.Integer)

    ip = db.Column(db.String(45))
    user_agent = db.Column(db.String(255))
    details = db.Column(db.Text)  # JSON

    organization = db.relationship("Organization")
    user = db.relationship("User")

    # התצוגה תמיד שואלת "מה קרה אצלי, לפי סדר יורד של זמן"
    __table_args__ = (
        db.Index("ix_activity_log_org_created", "organization_id", "created_at"),
    )

    @property
    def label(self):
        return action_label(self.action)

    @property
    def details_dict(self):
        if not self.details:
            return {}
        try:
            return json.loads(self.details)
        except ValueError:
            return {}

    @property
    def is_error(self):
        return (self.status_code or 0) >= 400

    @property
    def is_write(self):
        return self.method in WRITE_METHODS

    @property
    def actor(self):
        return self.user_label or "אנונימי"

    def to_dict(self):
        return {
            "id": self.id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "organization_id": self.organization_id,
            "user_id": self.user_id,
            "user_label": self.user_label,
            "user_role": self.user_role,
            "action": self.action,
            "label": self.label,
            "summary": self.summary,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "method": self.method,
            "path": self.path,
            "status_code": self.status_code,
            "duration_ms": self.duration_ms,
            "ip": self.ip,
            "details": self.details_dict,
        }

    def __repr__(self):
        return f"<ActivityLog {self.action} by {self.actor}>"


# ---------------------------------------------------------------------------
# כתיבה ללוג
# ---------------------------------------------------------------------------


def note(
    action=None,
    summary=None,
    entity_type=None,
    entity_id=None,
    actor=None,
    **details,
):
    """מוסיף פרטים לשורת הלוג של הבקשה הנוכחית.

    נקרא מתוך המסכים עצמם, במקום שבו ידוע מה באמת קרה - איזה רכב חופש,
    כמה תוצאות חזרו, כמה שורות יובאו. הפרטים נספחים לשורה האחת שנרשמת
    בסוף הבקשה, ולא יוצרים שורה נוספת.
    """
    if not has_request_context():
        return
    extra = g.setdefault("_activity_note", {})
    if action:
        extra["action"] = action
    if summary:
        extra["summary"] = str(summary)[:255]
    if entity_type:
        extra["entity_type"] = entity_type[:40]
    if entity_id is not None:
        extra["entity_id"] = entity_id
    if actor is not None:
        # מי שהתנתק כבר אינו current_user כשהשורה נכתבת - הצילום הזה
        # שומר את האירוע רשום על שמו במקום להיראות אנונימי.
        extra["actor"] = {
            "id": actor.id,
            "label": actor.phone or actor.email,
            "role": actor.role,
            "organization_id": actor.organization_id,
        }
    if details:
        extra.setdefault("details", {}).update(details)


def _clean(value):
    text = value if isinstance(value, str) else str(value)
    return text[:MAX_VALUE_LEN]


def _request_details():
    """מה שאפשר ללמוד מהבקשה עצמה, בלי ערכים רגישים."""
    details = {}
    args = {
        key: _clean(value)
        for key, value in list(request.args.items())[:MAX_ARGS]
        if key.lower() not in SENSITIVE_KEYS and value != ""
    }
    if args:
        details["args"] = args
    if request.method in WRITE_METHODS:
        fields = [key for key in request.form.keys() if key.lower() not in SENSITIVE_KEYS]
        if fields:
            details["form_fields"] = fields[:MAX_ARGS]
    uploads = [
        file.filename
        for file in request.files.values()
        if file and file.filename
    ]
    if uploads:
        details["files"] = uploads[:MAX_ARGS]
    if request.referrer:
        details["referrer"] = _clean(request.referrer)
    return details


def _current_user():
    """המשתמש המחובר, או None - גם כשאין בכלל שכבת התחברות בבקשה."""
    from flask_login import current_user

    try:
        if current_user and current_user.is_authenticated:
            return current_user
    except Exception:  # אין הקשר התחברות (למשל בבקשה שנפלה מוקדם)
        pass
    return None


def _build_entry(status_code):
    extra = g.get("_activity_note", {}) or {}
    user = _current_user()
    actor = extra.get("actor") or (
        {
            "id": user.id,
            "label": user.phone or user.email,
            "role": user.role,
            "organization_id": user.organization_id,
        }
        if user
        else {}
    )
    started = g.get("_activity_started")
    duration = int((time.perf_counter() - started) * 1000) if started else None

    details = _request_details()
    details.update(extra.get("details") or {})

    return ActivityLog(
        created_at=_now(),
        organization_id=actor.get("organization_id"),
        user_id=actor.get("id"),
        user_label=actor.get("label"),
        user_role=actor.get("role"),
        action=extra.get("action") or request.endpoint or "unknown",
        summary=extra.get("summary"),
        entity_type=extra.get("entity_type"),
        entity_id=extra.get("entity_id"),
        method=request.method,
        path=_clean(request.full_path.rstrip("?"))[:255],
        status_code=status_code,
        duration_ms=duration,
        ip=(request.remote_addr or "")[:45] or None,
        user_agent=(request.user_agent.string or "")[:255] or None,
        details=json.dumps(details, ensure_ascii=False) if details else None,
    )


def _should_log(app):
    if not app.config.get("ACTIVITY_LOG_ENABLED", True):
        return False
    if g.get("_activity_written"):
        return False
    if request.endpoint in SKIP_ENDPOINTS:
        return False
    return not request.path.startswith("/static/")


def _persist(app, status_code):
    """כותב את שורת הלוג. כישלון כאן לא מפיל את הבקשה."""
    g._activity_written = True
    try:
        # הבקשה כבר סיימה לשמור את שלה; מה שנשאר פתוח בסשן הוא שארית
        # שאיש לא התכוון לשמור, ואסור שהיא תיסחב פנימה עם שורת הלוג.
        db.session.rollback()
        db.session.add(_build_entry(status_code))
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        app.logger.warning("לא ניתן לכתוב ללוג השימוש: %s", exc)


def register_activity_log(app):
    """מחבר את הלוג למחזור החיים של הבקשה."""

    @app.before_request
    def _start_timer():
        # איפוס מפורש ולא רק אתחול: כשמוחזק app context חיצוני (בבדיקות,
        # ובסקריפטים) אותו g משרת כמה בקשות, ובלי האיפוס הבקשה השנייה
        # הייתה יורשת את הסימון "כבר נכתב" ואת הפרטים של הראשונה.
        g._activity_started = time.perf_counter()
        g._activity_note = {}
        g._activity_written = False

    @app.after_request
    def _log_request(response):
        if _should_log(app):
            _persist(app, response.status_code)
        return response

    @app.teardown_request
    def _log_failed_request(exc):
        # after_request לא רץ כשהבקשה נפלה בחריגה - השגיאה היא בדיוק
        # מה שרוצים לראות בלוג, ולכן היא נרשמת כאן.
        if exc is None or not has_request_context():
            return
        if _should_log(app):
            note(error=type(exc).__name__)
            _persist(app, 500)


# ---------------------------------------------------------------------------
# קריאה מהלוג
# ---------------------------------------------------------------------------


def search(
    organization_id=None,
    q=None,
    user_id=None,
    action=None,
    since=None,
    only_errors=False,
    only_writes=False,
):
    """שאילתת הלוג. organization_id=None משמעו כל הארגונים (מנהל מערכת)."""
    query = ActivityLog.query
    if organization_id is not None:
        query = query.filter(ActivityLog.organization_id == organization_id)
    if user_id:
        query = query.filter(ActivityLog.user_id == user_id)
    if action:
        query = query.filter(ActivityLog.action == action)
    if since is not None:
        query = query.filter(ActivityLog.created_at >= since)
    if only_errors:
        query = query.filter(ActivityLog.status_code >= 400)
    if only_writes:
        query = query.filter(ActivityLog.method.in_(WRITE_METHODS))
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(
            db.or_(
                ActivityLog.path.ilike(like),
                ActivityLog.summary.ilike(like),
                ActivityLog.user_label.ilike(like),
                ActivityLog.action.ilike(like),
                ActivityLog.details.ilike(like),
            )
        )
    return query.order_by(ActivityLog.created_at.desc(), ActivityLog.id.desc())


def _base(**filters):
    """אותם סינונים, בלי המיון - בשביל שאילתות הצבירה."""
    return search(**filters).order_by(None)


def summary_stats(**filters):
    """המספרים שמעל הטבלה: כמה אירועים, כמה משתמשים, כמה נכשלו."""
    base = _base(**filters)
    rows = base.with_entities(
        func.count(ActivityLog.id),
        func.count(func.distinct(ActivityLog.user_id)),
        func.avg(ActivityLog.duration_ms),
        func.max(ActivityLog.created_at),
    ).one()
    total, users, avg_ms, last_at = rows
    errors = _base(**{**filters, "only_errors": True}).with_entities(
        func.count(ActivityLog.id)
    ).scalar()
    writes = _base(**{**filters, "only_writes": True}).with_entities(
        func.count(ActivityLog.id)
    ).scalar()
    return {
        "total": total or 0,
        "users": users or 0,
        "errors": errors or 0,
        "writes": writes or 0,
        "avg_ms": int(avg_ms) if avg_ms else 0,
        "last_at": last_at,
    }


def top_actions(limit=8, **filters):
    """הפעולות הנפוצות ביותר בטווח שנבחר."""
    rows = (
        _base(**filters)
        .with_entities(ActivityLog.action, func.count(ActivityLog.id).label("count"))
        .group_by(ActivityLog.action)
        .order_by(func.count(ActivityLog.id).desc())
        .limit(limit)
        .all()
    )
    return [
        {"action": action, "label": action_label(action), "count": count}
        for action, count in rows
    ]


def top_users(limit=8, **filters):
    """מי השתמש הכי הרבה. מבקרים אנונימיים מקובצים לשורה אחת."""
    rows = (
        _base(**filters)
        .with_entities(
            ActivityLog.user_label,
            func.count(ActivityLog.id).label("count"),
            func.max(ActivityLog.created_at),
        )
        .group_by(ActivityLog.user_label)
        .order_by(func.count(ActivityLog.id).desc())
        .limit(limit)
        .all()
    )
    return [
        {"label": label or "אנונימי", "count": count, "last_at": last_at}
        for label, count, last_at in rows
    ]


def daily_counts(days=14, **filters):
    """כמה אירועים בכל יום - הרצועה שמראה אם השימוש עולה או יורד."""
    filters = {**filters, "since": _now() - timedelta(days=days - 1)}
    rows = (
        _base(**filters)
        .with_entities(
            func.date(ActivityLog.created_at).label("day"),
            func.count(ActivityLog.id),
        )
        .group_by("day")
        .all()
    )
    counted = {str(day): count for day, count in rows}
    today = _now().date()
    series = []
    for offset in range(days - 1, -1, -1):
        day = today - timedelta(days=offset)
        series.append({"day": day, "count": counted.get(day.isoformat(), 0)})
    return series


def known_actions(**filters):
    """הפעולות שקיימות בפועל בלוג - למילוי רשימת הסינון."""
    rows = (
        _base(**filters)
        .with_entities(ActivityLog.action)
        .group_by(ActivityLog.action)
        .order_by(ActivityLog.action)
        .all()
    )
    return [
        {"action": action, "label": action_label(action)} for (action,) in rows
    ]


def prune(days):
    """מוחק אירועים ישנים מ-days ימים. מחזיר כמה נמחקו."""
    cutoff = _now() - timedelta(days=days)
    deleted = ActivityLog.query.filter(ActivityLog.created_at < cutoff).delete(
        synchronize_session=False
    )
    db.session.commit()
    return deleted
