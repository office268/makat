"""התקנה כאפליקציה - מניפסט, service worker ואייקונים."""
import json


def test_manifest_is_valid_and_installable(client):
    """הדפדפן דורש name, icons, start_url ו-display כדי להציע התקנה."""
    response = client.get("/manifest.webmanifest")
    assert response.status_code == 200
    assert response.mimetype == "application/manifest+json"

    manifest = json.loads(response.get_data(as_text=True))
    assert manifest["name"]
    assert manifest["start_url"] == "/"
    assert manifest["display"] == "standalone"
    assert manifest["dir"] == "rtl" and manifest["lang"] == "he"

    sizes = {icon["sizes"] for icon in manifest["icons"]}
    assert {"192x192", "512x512"} <= sizes        # המינימום שדפדפנים דורשים
    purposes = {icon["purpose"] for icon in manifest["icons"]}
    assert "maskable" in purposes                  # אחרת האייקון נחתך באנדרואיד


def test_manifest_keeps_hebrew_readable(client):
    """ensure_ascii היה הופך את השם לרצף \\u - קריא למכונה, לא לאדם."""
    assert "מק" in client.get("/manifest.webmanifest").get_data(as_text=True)


def test_service_worker_is_served_from_root(client):
    """היקף ה-SW מוגבל לנתיב ההגשה; מ-/static הוא לא היה שולט על האתר."""
    response = client.get("/sw.js")
    assert response.status_code == 200
    assert "javascript" in response.mimetype
    assert response.headers["Service-Worker-Allowed"] == "/"


def test_service_worker_is_not_cached(client):
    """SW מקאש היה מקבע גרסה ישנה אצל המשתמש לנצח."""
    assert "no-cache" in client.get("/sw.js").headers["Cache-Control"]


def test_service_worker_does_not_cache_pages(client):
    """מחירים ומלאי חייבים להגיע מהרשת - קאש היה מציג נתונים ישנים כעדכניים."""
    body = client.get("/sw.js").get_data(as_text=True)
    assert 'request.method !== "GET"' in body       # POST אף פעם לא מהמטמון
    assert "/static/" in body                       # רק סטטי נשמר


def test_offline_page_renders(client):
    html = client.get("/offline").get_data(as_text=True)
    assert "אין חיבור" in html


def test_icons_exist_and_are_real_pngs(client):
    for name in ["icon-192.png", "icon-512.png", "icon-192-maskable.png",
                 "icon-512-maskable.png", "apple-touch-icon.png", "favicon.png"]:
        response = client.get(f"/static/icons/{name}")
        assert response.status_code == 200, name
        assert response.data[:8] == b"\x89PNG\r\n\x1a\n", name


def test_pages_link_the_manifest_and_register_the_worker(client):
    html = client.get("/").get_data(as_text=True)
    assert 'rel="manifest"' in html
    assert "serviceWorker" in html
    assert 'name="theme-color"' in html
