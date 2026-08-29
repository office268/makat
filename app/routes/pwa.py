"""התקנה כאפליקציה - מניפסט ו-service worker.

שניהם מוגשים מהשורש ולא מ-/static, כי היקף ה-service worker מוגבל
לנתיב שממנו הוא הוגש. קובץ ב-/static/sw.js היה שולט רק על /static.
"""
from flask import Blueprint, Response, current_app, render_template, url_for

pwa_bp = Blueprint("pwa", __name__)

APP_NAME = 'קטלוג מק"טים'
APP_DESCRIPTION = 'זיהוי מק"ט לחלקי רכב לפי מספר רישוי'
THEME = "#0c4a6e"


@pwa_bp.get("/manifest.webmanifest")
def manifest():
    def icon(name, size, purpose="any"):
        return {
            "src": url_for("static", filename=f"icons/{name}"),
            "sizes": f"{size}x{size}",
            "type": "image/png",
            "purpose": purpose,
        }

    return Response(
        current_app.json.dumps(
            {
                "id": "/",
                "name": APP_NAME,
                "short_name": 'מק"טים',
                "description": APP_DESCRIPTION,
                "lang": "he",
                "dir": "rtl",
                "start_url": "/",
                "scope": "/",
                "display": "standalone",
                "orientation": "portrait-primary",
                "background_color": "#f5f7f9",
                "theme_color": THEME,
                "icons": [
                    icon("icon-192.png", 192),
                    icon("icon-512.png", 512),
                    icon("icon-192-maskable.png", 192, "maskable"),
                    icon("icon-512-maskable.png", 512, "maskable"),
                ],
                "shortcuts": [
                    {
                        "name": 'זיהוי מק"ט',
                        "url": "/",
                        "icons": [icon("icon-192.png", 192)],
                    },
                    {
                        "name": "הקטלוג",
                        "url": "/parts",
                        "icons": [icon("icon-192.png", 192)],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        mimetype="application/manifest+json",
    )


@pwa_bp.get("/sw.js")
def service_worker():
    response = Response(
        render_template("sw.js", version=current_app.config["SW_VERSION"]),
        mimetype="application/javascript",
    )
    # אסור לקאשר את ה-service worker עצמו, אחרת גרסה חדשה לא תיקלט
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Service-Worker-Allowed"] = "/"
    return response


@pwa_bp.get("/offline")
def offline():
    return render_template("offline.html")
