"""אפליקציית ניהול קטלוג מק"טים לחלקי רכב."""
import logging
import os
import sys
from pathlib import Path

from flask import Flask, jsonify, render_template
from flask_migrate import Migrate
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import text
from werkzeug.middleware.proxy_fix import ProxyFix

from .activity import register_activity_log
from .config import Config
from .guards import register_read_only_guard
from .models import db

migrate = Migrate()
csrf = CSRFProtect()


def _configure_logging(app):
    """לוגים ל-stdout - זה מה ש-Railway אוסף ומציג."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("[%(asctime)s] %(levelname)s in %(module)s: %(message)s")
    )
    app.logger.handlers = [handler]
    app.logger.setLevel(logging.INFO if not app.debug else logging.DEBUG)


def _check_secret_key(app):
    """מונע העלאה לאוויר עם מפתח החתימה של הפיתוח.

    SECRET_KEY חותם את קוקי הסשן. ערך ברירת המחדל ידוע לכל מי שרואה את
    הריפו, ולכן בפרודקשן הוא חייב להיות מוחלף - אחרת אפשר לזייף סשנים.
    """
    if app.config["SECRET_KEY"] != "dev-secret-change-me" or app.config.get("TESTING"):
        return
    if app.config["IS_MANAGED_PLATFORM"]:
        raise RuntimeError(
            "SECRET_KEY לא הוגדר. הגדר משתנה סביבה SECRET_KEY בשירות לפני העלייה לאוויר. "
            "אפשר לייצר ערך עם: python -c \"import secrets; print(secrets.token_hex(32))\""
        )
    app.logger.warning("SECRET_KEY הוא ערך הפיתוח - אל תעלה כך לפרודקשן.")


def create_app(config_object=Config):
    app = Flask(__name__)
    app.config.from_object(config_object)

    _configure_logging(app)

    # מאחורי ה-proxy של Railway - בלי זה Flask רואה http ואת ה-IP של ה-proxy
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

    # תיקיית instance נחוצה רק ל-SQLite המקומי; בפרודקשן היא עשויה להיות לקריאה בלבד
    try:
        Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    except OSError:
        app.logger.debug("לא ניתן ליצור את תיקיית instance - ממשיכים.")

    db.init_app(app)
    migrate.init_app(app, db)
    _check_secret_key(app)

    from . import auth_models  # noqa: F401 - נדרש כדי ש-Alembic יראה את הטבלאות
    from . import activity  # noqa: F401
    from . import vehicle_catalog  # noqa: F401
    from . import fleet_stats  # noqa: F401
    from . import parts_discovery  # noqa: F401
    from . import live_lookup  # noqa: F401
    from .auth import auth_bp, login_manager

    login_manager.init_app(app)
    if app.config["CSRF_ENABLED"]:
        csrf.init_app(app)

    from .routes.activity import activity_bp
    from .routes.admin import admin_bp
    from .routes.api import api_bp
    from .routes.identify import identify_bp
    from .routes.pwa import pwa_bp
    from .routes.team import team_bp
    from .routes.web import web_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(web_bp)
    app.register_blueprint(team_bp)
    app.register_blueprint(identify_bp)
    app.register_blueprint(pwa_bp)
    app.register_blueprint(activity_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(api_bp, url_prefix="/api")

    # ה-API עובד עם מפתחות, לא עם קוקיז - CSRF לא רלוונטי שם
    if app.config["CSRF_ENABLED"]:
        csrf.exempt(api_bp)

    register_read_only_guard(app)
    register_activity_log(app)

    @app.template_global("csrf_field")
    def csrf_field():
        """שדה ה-CSRF המוסתר. מחזיר מחרוזת ריקה כשההגנה כבויה."""
        from markupsafe import Markup

        if not app.config["CSRF_ENABLED"]:
            return Markup("")
        from flask_wtf.csrf import generate_csrf

        return Markup(
            f'<input type="hidden" name="csrf_token" value="{generate_csrf()}">'
        )

    @app.template_filter("cross_refs_str")
    def cross_refs_str(part):
        from .services import format_cross_refs

        return format_cross_refs(part)

    @app.template_filter("fitments_str")
    def fitments_str(part):
        from .services import format_fitments

        return format_fitments(part)

    @app.template_filter("part_type_name")
    def part_type_name(key):
        """מפתח סוג חלק -> שם בעברית."""
        from .taxonomy import type_name

        return type_name(key)

    @app.template_filter("plate")
    def plate(value):
        """מספר רישוי בפורמט שמסתכלים עליו: 10732802 -> 107-32-802."""
        from .vehicles import format_plate

        return format_plate(value)

    @app.template_filter("localtime")
    def localtime(value, fmt="%d/%m/%Y %H:%M:%S"):
        """זמן שנשמר ב-UTC, מוצג בשעון המקומי."""
        from .activity import to_local

        local = to_local(value, app.config["DISPLAY_TIMEZONE"])
        return local.strftime(fmt) if local else "—"

    @app.template_filter("ils")
    def ils(value):
        """עיצוב מחיר בשקלים."""
        if value is None:
            return "-"
        return f"₪{value:,.2f}"

    @app.errorhandler(403)
    def forbidden(_error):
        return render_template("403.html"), 403

    @app.get("/healthz")
    def healthz():
        """בדיקת בריאות ל-Railway: האפליקציה חיה *ובסיס הנתונים נגיש*."""
        try:
            db.session.execute(text("SELECT 1"))
        except Exception as exc:
            app.logger.error("בדיקת בריאות נכשלה: %s", exc)
            return jsonify({"status": "unhealthy", "database": "unreachable"}), 503
        return jsonify({"status": "ok", "database": "ok"})

    # יצירת הטבלאות אוטומטית רק ב-SQLite המקומי. מול Postgres זה רץ
    # ב-preDeployCommand, כדי ששני ה-workers של gunicorn לא יתנגשו זה בזה.
    if app.config["AUTO_CREATE_TABLES"]:
        with app.app_context():
            db.create_all()

    @app.cli.command("init-db")
    def init_db_command():  # pragma: no cover - פקודת CLI
        """יוצר את טבלאות בסיס הנתונים."""
        db.create_all()
        print("הטבלאות נוצרו.")

    @app.cli.command("prune-activity")
    def prune_activity_command():  # pragma: no cover - פקודת CLI
        """מוחק אירועים ישנים מלוג השימוש (לפי ACTIVITY_LOG_RETENTION_DAYS)."""
        from .activity import prune

        days = app.config["ACTIVITY_LOG_RETENTION_DAYS"]
        print(f"נמחקו {prune(days)} אירועים ישנים מ-{days} ימים.")

    return app
