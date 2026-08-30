"""התחברות, הרשמה והרשאות."""
import re
from datetime import datetime, timezone
from functools import wraps

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import (
    LoginManager,
    current_user,
    login_required,
    login_user,
    logout_user,
)

from . import activity
from .auth_models import Organization, User
from .models import db

auth_bp = Blueprint("auth", __name__)
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "יש להתחבר כדי להמשיך."
login_manager.login_message_category = "info"

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD = 8


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id)) if user_id.isdigit() else None


@login_manager.unauthorized_handler
def _unauthorized():
    """בקשת API מקבלת JSON; בקשת דפדפן מופנית לדף ההתחברות."""
    from flask import jsonify

    if request.path.startswith("/api/"):
        return jsonify({"error": "נדרשת התחברות"}), 401
    flash(login_manager.login_message, login_manager.login_message_category)
    return redirect(url_for("auth.login", next=request.full_path))


def role_required(minimum):
    """חוסם גישה למי שאין לו לפחות את התפקיד הנתון."""

    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if not current_user.has_role(minimum):
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


def superadmin_required(view):
    """חוסם גישה לכל מי שאינו מנהל מערכת (ראה User.is_superadmin)."""

    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user.is_superadmin:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


# הדגל שאומר שכבר עברנו את מסך הפתיחה בסשן הזה
ENTERED = "entered"


def safe_target(target):
    """יעד הפניה מתוך פרמטר ב-URL, או המסך הראשי כשהוא לא בטוח.

    כתובת שאינה מתחילה ב-"/" (או מתחילה ב-"//") היא אתר חיצוני, ואסור
    לתת לפרמטר בשורת הכתובת לזרוק משתמש מחוץ למערכת אחרי התחברות.
    """
    if not target or not target.startswith("/") or target.startswith("//"):
        return url_for("identify.index")
    return target


def slugify(name):
    slug = re.sub(r"[^\w֐-׿-]+", "-", (name or "").strip().lower()).strip("-")
    return slug or "org"


def unique_slug(name):
    base = slugify(name)
    slug, suffix = base, 2
    while Organization.query.filter_by(slug=slug).first():
        slug = f"{base}-{suffix}"
        suffix += 1
    return slug


def _validate_signup(form):
    """מחזיר רשימת שגיאות. ריקה = תקין."""
    errors = []
    if not (form.get("organization_name") or "").strip():
        errors.append("יש להזין שם ארגון.")
    email = (form.get("email") or "").strip().lower()
    if not EMAIL_RE.match(email):
        errors.append("כתובת דוא\"ל לא תקינה.")
    elif User.query.filter_by(email=email).first():
        errors.append("כתובת הדוא\"ל כבר רשומה במערכת.")
    password = form.get("password") or ""
    if len(password) < MIN_PASSWORD:
        errors.append(f"הסיסמה חייבת להכיל לפחות {MIN_PASSWORD} תווים.")
    elif password != form.get("password_confirm"):
        errors.append("הסיסמאות אינן תואמות.")
    return errors


@auth_bp.route("/signup", methods=["GET", "POST"])
def signup():
    """הרשמת ארגון חדש. הנרשם הופך לבעלים שלו."""
    if current_user.is_authenticated:
        return redirect(url_for("identify.index"))

    if request.method == "POST":
        errors = _validate_signup(request.form)
        if errors:
            for message in errors:
                flash(message, "danger")
        else:
            name = request.form["organization_name"].strip()
            organization = Organization(
                name=name,
                slug=unique_slug(name),
                kind=request.form.get("kind") or "מוסך",
                phone=(request.form.get("phone") or "").strip() or None,
            )
            db.session.add(organization)
            db.session.flush()

            user = User(
                email=request.form["email"].strip().lower(),
                full_name=(request.form.get("full_name") or "").strip() or None,
                role="owner",
                organization=organization,
            )
            user.set_password(request.form["password"])
            db.session.add(user)
            db.session.commit()

            login_user(user)
            session[ENTERED] = True
            activity.note(
                action="auth.signup",
                summary=f"{organization.name} ({user.email})",
                entity_type="organization",
                entity_id=organization.id,
                kind=organization.kind,
            )
            flash(f"ברוך הבא, {organization.name}!", "success")
            return redirect(url_for("identify.index"))

    return render_template("auth/signup.html", form=request.form, kinds=Organization.KINDS)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("identify.index"))

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        user = User.query.filter_by(email=email).first()
        # הודעה זהה לשני המקרים - לא מסגירים אילו כתובות רשומות
        if user is None or not user.check_password(request.form.get("password")):
            activity.note(
                action="auth.login_failed", summary=email, reason="bad_credentials"
            )
            flash("דוא\"ל או סיסמה שגויים.", "danger")
        elif not user.is_active:
            activity.note(
                action="auth.login_failed", summary=email, reason="inactive"
            )
            flash("החשבון או הארגון מושבתים. פנה למנהל המערכת.", "warning")
        else:
            login_user(user, remember=request.form.get("remember") == "1")
            session[ENTERED] = True
            activity.note(
                action="auth.login",
                summary=f"{user.email} ({user.role_label})",
                entity_type="user",
                entity_id=user.id,
                remember=request.form.get("remember") == "1",
            )
            user.last_login_at = datetime.now(timezone.utc)
            db.session.commit()
            return redirect(safe_target(request.args.get("next")))

    return render_template("auth/login.html", form=request.form)


@auth_bp.before_app_request
def splash_gate():
    """מי שפותח את האפליקציה מקבל קודם את מסך הפתיחה.

    נחסם רק ה-GET הנקי של השורש. קישור עם מספר רישוי הוא תוצאה
    ששיתפו ו-POST הוא חיפוש - שניהם חייבים להגיע ליעד כמו שהם, ולא
    להתאדות לטובת מסך פתיחה. שאר המסכים נגישים ישירות: קישור עמוק
    שנשלח לעובד צריך להיפתח במקום שאליו הוא מצביע.

    הדגל יושב בסשן ולא בבסיס הנתונים: המכונית מקבלת את פניך פעם אחת
    בכל פתיחה של הדפדפן, ולא שוב בכל מעבר בין מסכים.
    """
    if request.method != "GET" or request.path != "/" or request.query_string:
        return None
    if session.get(ENTERED):
        return None
    return redirect(url_for("auth.welcome"))


@auth_bp.get("/welcome")
def welcome():
    """מסך הפתיחה: מכונית תלת-ממד מסתובבת שלחיצה עליה נכנסת לאפליקציה.

    מחובר או לא - הלחיצה מובילה לאותו מקום. המסך הראשי פתוח גם
    למבקר (בלי מחירים ומלאי), ולכן אין סיבה לחסום אותו בטופס.
    """
    target = safe_target(request.args.get("next"))
    return render_template(
        "auth/welcome.html", enter_url=url_for("auth.enter", next=target)
    )


@auth_bp.get("/enter")
def enter():
    """הלחיצה על המכונית: מסמנת שנכנסנו, וממשיכה לאפליקציה."""
    session[ENTERED] = True
    return redirect(safe_target(request.args.get("next")))


@auth_bp.post("/logout")
@login_required
def logout():
    activity.note(
        action="auth.logout",
        summary=current_user.email,
        entity_type="user",
        entity_id=current_user.id,
        actor=current_user,
    )
    logout_user()
    flash("התנתקת מהמערכת.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.get("/account")
@login_required
def account():
    return render_template("auth/account.html")
