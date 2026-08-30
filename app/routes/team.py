"""ניהול המשתמשים של הארגון.

בעלים מזמין עובדים, קובע להם תפקיד, ומשבית מי שעזב. כל הפעולות
מוגבלות לארגון של המשתמש המחובר - אין דרך להגיע למשתמש של ארגון אחר.
"""
import secrets
from datetime import datetime, timedelta, timezone

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user

from .. import activity, mailer
from ..auth import EMAIL_RE, MIN_PASSWORD, role_required
from ..auth_models import Invitation, User
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
    invitations = (
        Invitation.query.filter_by(
            organization_id=current_user.organization_id, accepted_at=None
        )
        .order_by(Invitation.created_at.desc())
        .all()
    )
    return render_template(
        "team/index.html", users=users, invitations=invitations,
        roles=User.ROLES, mail_configured=mailer.is_configured(),
    )


@team_bp.post("/team/invite")
@role_required("owner")
def invite():
    email = (request.form.get("email") or "").strip().lower()
    role = request.form.get("role") or "mechanic"

    if not EMAIL_RE.match(email):
        flash('כתובת דוא"ל לא תקינה.', "danger")
    elif role not in User.ROLES:
        flash("תפקיד לא מוכר.", "danger")
    elif User.query.filter_by(email=email).first():
        flash("כתובת הדוא\"ל כבר רשומה במערכת.", "warning")
    else:
        invitation = Invitation(
            email=email,
            role=role,
            organization_id=current_user.organization_id,
            token=secrets.token_urlsafe(32),
            expires_at=datetime.now(timezone.utc) + timedelta(days=14),
            invited_by_id=current_user.id,
        )
        db.session.add(invitation)
        db.session.commit()

        activity.note(
            summary=f"{email} ({invitation.role_label})",
            entity_type="invitation",
            entity_id=invitation.id,
            invited_email=email,
            role=role,
        )
        accept_url = url_for("team.accept", token=invitation.token, _external=True)
        if mailer.send_invitation(invitation, accept_url, current_user):
            flash(f"נשלחה הזמנה אל {email}.", "success")
        else:
            flash(
                f"נוצרה הזמנה עבור {email}, אך שליחת הדוא\"ל אינה מוגדרת — "
                "העתק את הקישור מהטבלה ושלח אותו ידנית.",
                "warning",
            )
    return redirect(url_for("team.index"))


@team_bp.post("/team/invite/<int:invitation_id>/revoke")
@role_required("owner")
def revoke(invitation_id):
    invitation = db.session.get(Invitation, invitation_id)
    if invitation is None or invitation.organization_id != current_user.organization_id:
        abort(404)
    activity.note(
        summary=invitation.email, entity_type="invitation", entity_id=invitation.id
    )
    db.session.delete(invitation)
    db.session.commit()
    flash("ההזמנה בוטלה.", "info")
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
            summary=f"{user.email}: {previous} → {user.role_label}",
            entity_type="user",
            entity_id=user.id,
            role=role,
        )
        flash(f"{user.email} הוגדר כ{user.role_label}.", "success")
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
            summary=f"{user.email} {'הופעל' if user.active else 'הושבת'}",
            entity_type="user",
            entity_id=user.id,
            active=user.active,
        )
        flash(
            f"{user.email} {'הופעל' if user.active else 'הושבת'}.",
            "success" if user.active else "info",
        )
    return redirect(url_for("team.index"))


def _owner_count():
    """כמה בעלים פעילים יש בארגון - שומר שלא יינעל בלי אף בעלים."""
    return User.query.filter_by(
        organization_id=current_user.organization_id, role="owner", active=True
    ).count()


@team_bp.route("/invite/<token>", methods=["GET", "POST"])
def accept(token):
    """קבלת הזמנה - המוזמן קובע סיסמה ונכנס לארגון."""
    if current_user.is_authenticated:
        flash("יש להתנתק לפני קבלת הזמנה.", "warning")
        return redirect(url_for("identify.index"))

    invitation = Invitation.query.filter_by(token=token, accepted_at=None).first()
    if invitation is None or invitation.is_expired:
        return render_template("team/invalid_invite.html"), 404

    if request.method == "POST":
        password = request.form.get("password") or ""
        if len(password) < MIN_PASSWORD:
            flash(f"הסיסמה חייבת להכיל לפחות {MIN_PASSWORD} תווים.", "danger")
        elif password != request.form.get("password_confirm"):
            flash("הסיסמאות אינן תואמות.", "danger")
        elif User.query.filter_by(email=invitation.email).first():
            flash("כתובת הדוא\"ל כבר רשומה במערכת.", "danger")
        else:
            user = User(
                email=invitation.email,
                full_name=(request.form.get("full_name") or "").strip() or None,
                role=invitation.role,
                organization_id=invitation.organization_id,
            )
            user.set_password(password)
            invitation.accepted_at = datetime.now(timezone.utc)
            db.session.add(user)
            db.session.commit()
            login_user(user)
            activity.note(
                summary=f"{user.email} הצטרף כ{user.role_label}",
                entity_type="user",
                entity_id=user.id,
                invitation_id=invitation.id,
            )
            flash(f"ברוך הבא ל{user.organization.name}!", "success")
            return redirect(url_for("identify.index"))

    return render_template("team/accept.html", invitation=invitation)
