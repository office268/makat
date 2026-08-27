"""אפליקציית ניהול קטלוג מק"טים לחלקי רכב."""
from pathlib import Path

from flask import Flask

from .config import Config
from .models import db


def create_app(config_object=Config):
    app = Flask(__name__)
    app.config.from_object(config_object)

    # ודא שתיקיית ה-instance קיימת עבור קובץ ה-SQLite
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    db.init_app(app)

    from .routes.api import api_bp
    from .routes.web import web_bp

    app.register_blueprint(web_bp)
    app.register_blueprint(api_bp, url_prefix="/api")

    @app.template_filter("ils")
    def ils(value):
        """עיצוב מחיר בשקלים."""
        if value is None:
            return "-"
        return f"₪{value:,.2f}"

    with app.app_context():
        db.create_all()

    @app.cli.command("seed")
    def seed_command():  # pragma: no cover - פקודת CLI
        """טוען נתוני דמו לקטלוג."""
        from scripts.seed import seed

        seed(app)

    return app
