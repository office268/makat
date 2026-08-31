"""ניהול המשתמשים של הארגון.

בעלים מוסיף מספר טלפון, קובע לו תפקיד, ומשבית מי שעזב. כל הפעולות
מוגבלות לארגון של המשתמש המחובר - אין דרך להגיע למשתמש של ארגון אחר.

המסך הזה *הוא* רשימת המורשים: מרגע שהזהות היא מספר טלפון, הוספת שורה
כאן היא מתן הגישה, והשבתתה היא שלילתה. אין הזמנה בדוא"ל ואין סיסמה
לקבוע - העובד מקבל את המספר שלו, וזה כל מה שהוא מזין.
"""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from .. import activity, phones
from ..auth import role_required
from ..auth_models import User
from ..models import db

team_bp = Blueprint("team", __name__)


def _org_user_or_404(user_id):
    """שולף משתמש מהארגון של המחובר בלבד."""
    user = db.session.get(User, user_id)
    if user is None or user.organization_id != current_user.organization_id:
        abort(404)
    return user


@team_bp.get("/team")
@role_required("owner")
def index():
    users = (
        User.query.filter_by(organization_id=current_user.organization_id)
        .order_by(User.created_at)
        .all()
    )
    return render_template("team/index.html", users=users, roles=User.ROLES)


@team_bp.post("/team/add")
@role_required("owner")
def add_user():
    """הוספת מורשה: מספר טלפון, שם ותפקיד.

    המספר נשמר מנורמל (ראה app/phones.py), אחרת אותו אדם היה נכנס
    כשהקליד 052-1234567 ונדחה כשהקליד 0521234567.
    """
    typed = (request.form.get("phone") or "").strip()
    phone = phones.normalize(typed)
    role = request.form.get("role") or "mechanic"
    full_name = (request.form.get("full_name") or "").strip() or None

    if phone is None:
        flash("מספר הטלפון אינו תקין.", "danger")
    elif role not in User.ROLES:
        flash("תפקיד לא מוכר.", "danger")
    elif User.query.filter_by(phone=phone).first():
        flash("המספר כבר רשום במערכת.", "warning")
    else:
        user = User(
            phone=phone,
            full_name=full_name,
            role=role,
            organization_id=current_user.organization_id,
        )
        db.session.add(user)
        db.session.commit()
        activity.note(
            summary=f"{user.display_name} ({user.role_label})",
            entity_type="user",
            entity_id=user.id,
            phone=phone,
            role=role,
        )
        flash(f"{user.display_name} יכול להיכנס עכשיו.", "success")
    return redirect(url_for("team.index"))


@team_bp.post("/team/<int:user_id>/role")
@role_required("owner")
def change_role(user_id):
    user = _org_user_or_404(user_id)
    role = request.form.get("role")
    if role not in User.ROLES:
        flash("תפקיד לא מוכר.", "danger")
    elif user.id == current_user.id:
        flash("אי אפשר לשנות את התפקיד של עצמך.", "warning")
    elif user.role == "owner" and _owner_count() <= 1:
        flash("חייב להישאר לפחות בעלים אחד בארגון.", "warning")
    else:
        previous = user.role_label
        user.role = role
        db.session.commit()
        activity.note(
            summary=f"{user.display_name}: {previous} → {user.role_label}",
            entity_type="user",
            entity_id=user.id,
            role=role,
        )
        flash(f"{user.display_name} הוגדר כ{user.role_label}.", "success")
    return redirect(url_for("team.index"))


@team_bp.post("/team/<int:user_id>/toggle")
@role_required("owner")
def toggle_active(user_id):
    user = _org_user_or_404(user_id)
    if user.id == current_user.id:
        flash("אי אפשר להשבית את עצמך.", "warning")
    elif user.active and user.role == "owner" and _owner_count() <= 1:
        flash("חייב להישאר לפחות בעלים פעיל אחד בארגון.", "warning")
    else:
        user.active = not user.active
        db.session.commit()
        activity.note(
            summary=f"{user.display_name} {'הופעל' if user.active else 'הושבת'}",
            entity_type="user",
            entity_id=user.id,
            active=user.active,
        )
        flash(
            f"{user.display_name} {'הופעל' if user.active else 'הושבת'}.",
            "success" if user.active else "info",
        )
    return redirect(url_for("team.index"))


def _owner_count():
    """כמה בעלים פעילים יש בארגון - שומר שלא יינעל בלי אף בעלים."""
    return User.query.filter_by(
        organization_id=current_user.organization_id, role="owner", active=True
    ).count()
