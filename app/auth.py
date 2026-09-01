"""הזדהות והרשאות.

ההזדהות היא שדה אחד: מספר טלפון. מי שהמספר שלו נמצא בטבלת המשתמשים
נכנס, וכל השאר עוצרים בדלת. אין סיסמה, אין הרשמה עצמית ואין דוא"ל -
רשימת המורשים היא טבלת המשתמשים עצמה, ומנהלים אותה במסך הצוות.
"""
from urllib.parse import urlencode, urlparse
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

from . import activity, phones
from .auth_models import User
from .models import db

auth_bp = Blueprint("auth", __name__)
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "יש להזדהות כדי להמשיך."
login_manager.login_message_category = "info"


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id)) if user_id.isdigit() else None


@login_manager.unauthorized_handler
def _unauthorized():
    """בקשת API מקבלת JSON; בקשת דפדפן מופנית למסך ההזדהות."""
    from flask import jsonify

    if request.path.startswith("/api/"):
        return jsonify({"error": "נדרשת הזדהות"}), 401
    flash(login_manager.login_message, login_manager.login_message_category)
    return redirect(with_next("auth.login", here()))


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


# אסימון חד-פעמי שנשרף בכניסה. הוא מה שמונע לולאה: גם כשהדפדפן אינו
# שולח Referer, הלחיצה על המכונית עוברת - בדיוק פעם אחת.
PASS_ONCE = "may_enter"

# היעדרות ארוכה מזו נחשבת פתיחה מחדש, גם בלי בקשה לשרת. ראה app.js.
SPLASH_AWAY_SECONDS = 30


def safe_target(target):
    """יעד הפניה מתוך פרמטר ב-URL, או המסך הראשי כשהוא לא בטוח.

    כתובת שאינה מתחילה ב-"/" (או מתחילה ב-"//") היא אתר חיצוני, ואסור
    לתת לפרמטר בשורת הכתובת לזרוק משתמש מחוץ למערכת אחרי התחברות.

    הלוכסן ההפוך נפסל גם הוא, ולא מתוך זהירות יתר: לפי תקן ה-URL
    דפדפן מתרגם "\\" ל-"/" לפני שהוא קורא את הכתובת, ולכן
    ‎/login?next=/\\evil.com עבר את הבדיקה שלמעלה כנתיב פנימי
    והדפדפן פתח ממנו //evil.com - הפניה החוצה בדיוק כמו זו שנחסמה.
    """
    if not target or not target.startswith("/"):
        return url_for("identify.index")
    if target.startswith(("//", "/\\")):
        return url_for("identify.index")
    return target


def here():
    """הכתובת הנוכחית כיעד לחזרה אחרי הזדהות.

    full_path מוסיף "?" גם לבקשה שאין בה פרמטרים, ו-/parts? הוא יעד
    מכוער שאין סיבה לגרור אותו הלוך ושוב.
    """
    return request.full_path if request.query_string else request.path


def with_next(endpoint, target):
    """כתובת של מסך בשער, עם היעד לחזרה אליו כפרמטר מקודד.

    url_for משאיר "/" ו-"?" כמות שהם בערך של פרמטר, וכך נולדה בייצור
    הכתובת /login?next=/parts? - שני סימני שאלה באותה כתובת. הדפדפן
    עוד הסתדר איתה, אבל בדרך יש מי שמנרמל: הבקשה חזרה בלוגים כ-
    /login%3Fnext=/parts, כלומר נתיב אחד שאין לו מסלול, ומי שביקש
    להזדהות קיבל 404 במקום את הטופס. קידוד מלא של הערך לא משאיר
    מקום לפרשנות.
    """
    return f"{url_for(endpoint)}?{urlencode({'next': target})}"


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """הזדהות. שדה אחד, מספר טלפון.

    שלוש דחיות אפשריות, וכל אחת אומרת את שמה: מספר שאינו מספר, מספר
    שאינו ברשימת המורשים, ומשתמש או ארגון שהושבתו. ההסתרה שנהוגה
    בטופס סיסמה ("אחד מהשניים שגוי") לא מרוויחה כאן דבר: מי שמנחש
    מספרים יגלה ממילא, ומכונאי שעומד מול טופס שלא אומר לו מה קרה
    יתקשר למנהל במקום לתקן ספרה.
    """
    if current_user.is_authenticated:
        return redirect(safe_target(request.args.get("next")))

    if request.method == "POST":
        typed = (request.form.get("phone") or "").strip()
        phone = phones.normalize(typed)
        user = User.query.filter_by(phone=phone).first() if phone else None

        if phone is None:
            activity.note(
                action="auth.login_failed", summary=typed[:40], reason="malformed"
            )
            flash("מספר הטלפון אינו תקין.", "danger")
        elif user is None:
            activity.note(
                action="auth.login_failed", summary=phone, reason="unknown_phone"
            )
            flash("המספר הזה אינו מורשה להיכנס. פנה למנהל המערכת.", "danger")
        elif not user.is_active:
            activity.note(
                action="auth.login_failed", summary=phone, reason="inactive"
            )
            flash("החשבון או הארגון מושבתים. פנה למנהל המערכת.", "warning")
        else:
            # הזדהות נשמרת: מכשיר במוסך אינו נשאל בכל פתיחה. מסך
            # הפתיחה עדיין מקדם כל פתיחה - הוא הדלת, לא השומר.
            login_user(user, remember=True)
            session[PASS_ONCE] = True
            activity.note(
                action="auth.login",
                summary=f"{user.display_name} ({user.role_label})",
                entity_type="user",
                entity_id=user.id,
            )
            user.last_login_at = datetime.now(timezone.utc)
            db.session.commit()
            return redirect(safe_target(request.args.get("next")))

    return render_template("auth/login.html", form=request.form)


def _opens_the_app(req):
    """ה-GET הנקי של השורש - הדרך שבה פותחים את האפליקציה.

    קישור עם מספר רישוי הוא תוצאה ששיתפו ו-POST הוא חיפוש; שניהם
    חייבים להגיע ליעד כמו שהם ולא להתאדות לטובת מסך פתיחה.
    """
    return req.method == "GET" and req.path == "/" and not req.query_string


def _from_inside(req):
    """הגעה ממסך אחר של האפליקציה, להבדיל מפתיחה שלה.

    זה ההבדל בין "ניווט" ל"פתיחה", והוא מה שנמדד כאן. מדידה לפי זמן
    שגתה: מי שסגר ופתח את האפליקציה אחרי דקה פתח אותה מחדש לכל דבר,
    גם אם השעון בקושי זז.

    ההשוואה על ה-host בלבד ולא על הכתובת המלאה: מאחורי ה-proxy יש
    אי-התאמות של http מול https, והן לא אמורות להיחשב אתר אחר.
    """
    if not req.referrer:
        return False
    return urlparse(req.referrer).netloc == urlparse(req.host_url).netloc


@auth_bp.before_app_request
def splash_gate():
    """כל פתיחה של האפליקציה מתחילה במסך הפתיחה.

    שלושה מצבים, בסדר הזה:

      אסימון כניסה   מי שלחץ על המכונית נכנס. האסימון נשרף מיד, ולכן
                     הוא פותח את הדלת בדיוק פעם אחת ולא יוצר לולאה
                     כשהדפדפן אינו שולח Referer
      הגעה מבפנים    ניווט ממסך אחר של האפליקציה אינו פתיחה שלה
      כל השאר        פתיחה: אייקון במסך הבית, סימנייה, כתובת שהוקלדה,
                     לשונית חדשה - ושם המכונית מקבלת את הפנים

    נחסם רק השורש. קישור עמוק שנשלח לעובד מצביע על מסך מסוים, ולכן
    הוא נפתח שם - אחרי הזדהות, שאותה מבקש השער הבא.
    """
    if not _opens_the_app(request):
        return None
    if session.pop(PASS_ONCE, False):
        return None
    if _from_inside(request):
        return None
    return redirect(url_for("auth.welcome"))


# מה שפתוח למי שעוד לא הזדהה: הדלת עצמה, והקבצים שהדפדפן מושך בעצמו
# כדי להציג אותה. כל השאר - כולל הקטלוג - נמצא מעבר לה.
PUBLIC_ENDPOINTS = frozenset(
    {
        "auth.welcome",
        "auth.enter",
        "auth.login",
        "static",
        "pwa.manifest",
        "pwa.service_worker",
        "pwa.offline",
        "healthz",
    }
)


@auth_bp.before_app_request
def identification_gate():
    """בלי הזדהות אין אפליקציה.

    רץ אחרי splash_gate, ולכן פתיחה של האפליקציה עדיין פוגשת קודם את
    המכונית: המכונית היא הדלת, וזה השומר שמאחוריה.

    כתובת שאינה מוכרת (endpoint ריק) ממשיכה ל-404 שלה. הפניה להזדהות
    הייתה הופכת כל שגיאת כתובת לטופס, ומסתירה שהקישור פשוט שבור.
    """
    if current_user.is_authenticated:
        return None
    if request.endpoint in PUBLIC_ENDPOINTS or request.endpoint is None:
        return None
    return login_manager.unauthorized()


@auth_bp.after_app_request
def no_store_at_the_door(response):
    """דלת הכניסה לא נשמרת במטמון הדפדפן.

    בלעדי זה הדפדפן מגיש את השורש מהמטמון שלו בלי לשאול את השרת,
    והשער - שרץ רק כשמגיעה בקשה - לא מקבל הזדמנות לפעול.
    """
    if _opens_the_app(request):
        response.headers["Cache-Control"] = "no-store"
    return response


@auth_bp.app_context_processor
def splash_settings():
    """מה שהדף צריך כדי להגיע לאותה מסקנה כמו השרת.

    הוא זקוק לזה כי יש חזרה לאפליקציה שלא מייצרת בקשה כלל: דפדפן
    שמשחזר את הדף מהזיכרון. ראה static/js/app.js.
    """
    return {
        "splash_away_seconds": SPLASH_AWAY_SECONDS,
        "splash_url": url_for("auth.welcome"),
    }


@auth_bp.get("/welcome")
def welcome():
    """מסך הפתיחה: מכונית תלת-ממד מסתובבת שלחיצה עליה נכנסת לאפליקציה.

    הלחיצה מובילה לאותו מקום לכולם - ‎/enter מחליט שם אם היא נכנסת
    פנימה או עוצרת בשדה הטלפון. המסך הזה עצמו פתוח לכל אחד; הוא לא
    מסגיר דבר, ובלעדיו לא הייתה דרך להגיע לשדה.
    """
    target = safe_target(request.args.get("next"))
    return render_template(
        "auth/welcome.html", enter_url=with_next("auth.enter", target)
    )


@auth_bp.get("/enter")
def enter():
    """הלחיצה על המכונית.

    מי שכבר הזדהה נכנס ישר, עם אסימון כניסה יחיד שפותח את השורש בלי
    לחזור למסך הפתיחה. מי שלא - פוגש את שדה הטלפון, ומשם ממשיך אל
    אותו יעד עצמו.
    """
    target = safe_target(request.args.get("next"))
    if not current_user.is_authenticated:
        return redirect(with_next("auth.login", target))
    session[PASS_ONCE] = True
    return redirect(target)


@auth_bp.post("/logout")
@login_required
def logout():
    activity.note(
        action="auth.logout",
        summary=current_user.display_name,
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
