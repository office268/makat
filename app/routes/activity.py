"""מסך לוג השימוש - מה קורה במערכת, מי עשה את זה ומתי.

הלוג הוא נתון של הארגון: בעלים רואה את מה שקרה אצלו בלבד. מנהל
מערכת יכול לפתוח את התצוגה לכל הארגונים (scope=all), כי הוא ממילא
חוצה ארגונים - ורק הוא.
"""
import csv
import io
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    render_template,
    request,
)
from flask_login import current_user, login_required

from .. import activity
from ..activity import ActivityLog
from ..auth_models import User
from ..models import db

activity_bp = Blueprint("activity", __name__)

# טווחי הזמן שאפשר לבחור במסך. 0 = הכל, בלי הגבלת תאריך.
RANGES = ((1, "היום"), (7, "שבוע"), (30, "חודש"), (90, "רבעון"), (0, "הכל"))

CSV_COLUMNS = [
    "created_at",
    "user_label",
    "user_role",
    "organization_id",
    "action",
    "label",
    "summary",
    "method",
    "path",
    "status_code",
    "duration_ms",
    "ip",
    "details",
]


def _log_reader(view):
    """הלוג פתוח לבעלים של הארגון, ולמנהל מערכת."""

    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not (current_user.can_manage_users or current_user.is_superadmin):
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def _all_orgs():
    """האם התצוגה הנוכחית חוצה ארגונים. רק מנהל מערכת יכול לבקש את זה."""
    return current_user.is_superadmin and request.args.get("scope") == "all"


def _filters():
    days = request.args.get("days", 7, type=int)
    if days not in {value for value, _ in RANGES}:
        days = 7
    only = request.args.get("only", "")
    return {
        "organization_id": None if _all_orgs() else current_user.organization_id,
        "q": request.args.get("q", "").strip() or None,
        "user_id": request.args.get("user_id", type=int),
        "action": request.args.get("action", "").strip() or None,
        "since": datetime.now(timezone.utc) - timedelta(days=days) if days else None,
        "only_errors": only == "errors",
        "only_writes": only == "writes",
    }, days, only


@activity_bp.get("/activity")
@_log_reader
def index():
    filters, days, only = _filters()
    page = request.args.get("page", 1, type=int)
    pagination = activity.search(**filters).paginate(
        page=page, per_page=current_app.config["ACTIVITY_PER_PAGE"], error_out=False
    )
    # רשימת המשתמשים לסינון: של הארגון, או כל מי שנרשם בלוג בתצוגה חוצת-ארגונים
    users = (
        User.query.order_by(User.phone).all()
        if _all_orgs()
        else User.query.filter_by(
            organization_id=current_user.organization_id
        ).order_by(User.phone).all()
    )
    return render_template(
        "activity.html",
        pagination=pagination,
        entries=pagination.items,
        stats=activity.summary_stats(**filters),
        daily=activity.daily_counts(days=14, **filters),
        top_actions=activity.top_actions(**filters),
        top_users=activity.top_users(**filters),
        actions=activity.known_actions(**filters),
        users=users,
        ranges=RANGES,
        days=days,
        only=only,
        all_orgs=_all_orgs(),
        query=request.args.get("q", ""),
        selected_action=filters["action"],
        selected_user=filters["user_id"],
        retention_days=current_app.config["ACTIVITY_LOG_RETENTION_DAYS"],
        enabled=current_app.config["ACTIVITY_LOG_ENABLED"],
    )


@activity_bp.get("/activity.csv")
@_log_reader
def export_csv():
    """ייצוא הלוג המסונן - לניתוח באקסל או לשמירה מחוץ למערכת."""
    filters, _, _ = _filters()
    rows = activity.search(**filters).limit(10000).all()

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    for entry in rows:
        writer.writerow(
            {
                "created_at": entry.created_at.isoformat() if entry.created_at else "",
                "user_label": entry.user_label or "",
                "user_role": entry.user_role or "",
                "organization_id": entry.organization_id or "",
                "action": entry.action,
                "label": entry.label,
                "summary": entry.summary or "",
                "method": entry.method or "",
                "path": entry.path or "",
                "status_code": entry.status_code or "",
                "duration_ms": entry.duration_ms or "",
                "ip": entry.ip or "",
                "details": entry.details or "",
            }
        )
    return Response(
        "﻿" + buffer.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=activity_log.csv"},
    )


@activity_bp.get("/activity/<int:entry_id>")
@_log_reader
def entry(entry_id):
    """אירוע בודד כ-JSON - לפתיחת השורה בטבלה בלי לטעון את הדף מחדש."""
    row = db.session.get(ActivityLog, entry_id)
    if row is None:
        abort(404)
    if not _all_orgs() and row.organization_id != current_user.organization_id:
        abort(404)
    return row.to_dict()
