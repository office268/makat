"""ארגונים, משתמשים והרשאות."""
import pytest

from app import auth
from app.auth_models import Organization, User
from app.models import db


def _age_session(client, seconds):
    """מזקין את חותמת הפעילות, במקום להמתין בפועל."""
    with client.session_transaction() as flask_session:
        flask_session[auth.LAST_SEEN] -= seconds


@pytest.fixture
def org(app):
    with app.app_context():
        organization = Organization(name="מוסך בדיקה", slug="test-garage")
        db.session.add(organization)
        db.session.flush()
        for email, role in [("owner@t.test", "owner"),
                            ("manager@t.test", "manager"),
                            ("mech@t.test", "mechanic")]:
            user = User(email=email, role=role, organization=organization)
            user.set_password("password123")
            db.session.add(user)
        db.session.commit()
        yield organization


def login(client, email):
    return client.post("/login", data={"email": email, "password": "password123"})


# ---------- סיסמאות ----------

def test_password_is_hashed_not_stored(app, org):
    with app.app_context():
        user = User.query.filter_by(email="owner@t.test").first()
        assert "password123" not in user.password_hash
        assert user.check_password("password123")
        assert not user.check_password("wrong")


# ---------- תפקידים ----------

@pytest.mark.parametrize(
    "role,can_edit,can_manage_users",
    [("owner", True, True), ("manager", True, False), ("mechanic", False, False)],
)
def test_role_capabilities(app, org, role, can_edit, can_manage_users):
    with app.app_context():
        user = User.query.filter_by(role=role).first()
        assert user.can_edit_catalog is can_edit
        assert user.can_manage_users is can_manage_users


def test_disabled_organization_deactivates_its_users(app, org):
    with app.app_context():
        organization = Organization.query.filter_by(slug="test-garage").first()
        organization.is_active = False
        db.session.commit()
        assert User.query.filter_by(email="owner@t.test").first().is_active is False


# ---------- אכיפה על נקודות הקצה ----------

def test_anonymous_cannot_write(client, org):
    assert client.post("/api/parts", json={"part_number": "X", "name_he": "y"}).status_code == 401
    assert client.delete("/api/parts/1").status_code == 401
    assert client.get("/parts/new").status_code == 302        # הפניה לדף התחברות
    assert client.post("/import").status_code == 302


def test_anonymous_can_still_read(client, org):
    for path in ["/", "/parts", "/dashboard", "/api/parts", "/api/stats", "/export.csv"]:
        assert client.get(path).status_code == 200, path


def test_mechanic_cannot_write_but_can_read(client, org):
    login(client, "mech@t.test")
    assert client.post("/api/parts", json={"part_number": "M", "name_he": "y"}).status_code == 403
    assert client.get("/parts").status_code == 200


def test_manager_can_write(client, org):
    login(client, "manager@t.test")
    assert client.post(
        "/api/parts", json={"part_number": "MGR-1", "name_he": "רפידות"}
    ).status_code == 201


# ---------- הרשמה והתחברות ----------

def test_signup_creates_organization_with_owner(client, app):
    client.post("/signup", data={
        "organization_name": "מוסך חדש", "email": "new@t.test",
        "password": "longenough1", "password_confirm": "longenough1"})
    with app.app_context():
        organization = Organization.query.filter_by(name="מוסך חדש").first()
        assert organization is not None
        assert organization.users[0].role == "owner"


@pytest.mark.parametrize("field,value", [
    ("email", "not-an-email"),
    ("password", "short"),
])
def test_signup_rejects_invalid_input(client, app, field, value):
    data = {"organization_name": "ארגון", "email": "ok@t.test",
            "password": "longenough1", "password_confirm": "longenough1"}
    data[field] = value
    if field == "password":
        data["password_confirm"] = value
    client.post("/signup", data=data)
    with app.app_context():
        assert Organization.query.filter_by(name="ארגון").first() is None


def test_signup_rejects_duplicate_email(client, app, org):
    client.post("/signup", data={
        "organization_name": "ארגון כפול", "email": "owner@t.test",
        "password": "longenough1", "password_confirm": "longenough1"})
    with app.app_context():
        assert Organization.query.filter_by(name="ארגון כפול").first() is None


def test_login_rejects_bad_password(client, org):
    response = client.post(
        "/login", data={"email": "owner@t.test", "password": "nope"},
        follow_redirects=True)
    assert "שגויים" in response.get_data(as_text=True)
    assert client.post("/api/parts", json={"part_number": "X", "name_he": "y"}).status_code == 401


def test_logout_ends_the_session(client, org):
    login(client, "manager@t.test")
    assert client.post("/api/parts", json={"part_number": "L-1", "name_he": "x"}).status_code == 201
    client.post("/logout")
    assert client.post("/api/parts", json={"part_number": "L-2", "name_he": "x"}).status_code == 401


def test_login_redirect_ignores_external_next(client, org):
    """פרמטר next לא יכול להפנות לאתר חיצוני."""
    response = client.post("/login?next=https://evil.example/x",
                           data={"email": "owner@t.test", "password": "password123"})
    assert "evil.example" not in response.headers.get("Location", "")


# ---------- מסך הפתיחה ----------

def test_welcome_page_renders_the_car(client):
    response = client.get("/welcome")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'id="car-canvas"' in html
    assert "js/car3d.js" in html


def test_welcome_car_links_into_the_app(client):
    """הלחיצה על המכונית היא קישור אמיתי, גם בלי JavaScript."""
    html = client.get("/welcome").get_data(as_text=True)
    assert 'id="enter-app" href="/enter?next=/"' in html


def test_welcome_honours_next_but_not_external_targets(client):
    html = client.get("/welcome?next=/parts").get_data(as_text=True)
    assert 'id="enter-app" href="/enter?next=/parts"' in html

    html = client.get("/welcome?next=https://evil.example/x").get_data(as_text=True)
    assert "evil.example" not in html
    assert 'id="enter-app" href="/enter?next=/"' in html


# ---------- שער הכניסה ----------

def test_visitor_meets_the_car_before_the_app(visitor):
    """הדבר הראשון שרואים בפתיחת האפליקציה הוא המכונית."""
    response = visitor.get("/")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/welcome")


def test_clicking_the_car_opens_the_app_and_does_not_ask_again(visitor):
    enter = visitor.get("/enter?next=/")
    assert enter.status_code == 302
    assert enter.headers["Location"] == "/"
    # מכאן והלאה נכנסים ישר לאפליקציה, בלי לעבור שוב במסך הפתיחה
    assert visitor.get("/").status_code == 200


def test_browsing_keeps_the_splash_away(visitor):
    """שוטטות בין המסכים היא פעילות - היא לא מחזירה את מסך הפתיחה."""
    visitor.get("/enter?next=/")
    _age_session(visitor, seconds=auth.SPLASH_IDLE_SECONDS - 30)
    visitor.get("/parts")
    _age_session(visitor, seconds=auth.SPLASH_IDLE_SECONDS - 30)
    assert visitor.get("/").status_code == 200


def test_the_car_returns_after_a_break(visitor):
    """רבע שעה בלי פעילות אומרת שהמשתמש הלך; החזרה היא פתיחה חדשה."""
    visitor.get("/enter?next=/")
    assert visitor.get("/").status_code == 200

    _age_session(visitor, seconds=auth.SPLASH_IDLE_SECONDS + 60)
    response = visitor.get("/")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/welcome")


def test_quiet_requests_do_not_count_as_activity(visitor):
    """בדיקת בריאות אינה משתמש שעובד, ואסור לה להסתיר את מסך הפתיחה."""
    visitor.get("/healthz")
    response = visitor.get("/")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/welcome")


def test_the_door_is_never_cached_by_the_browser(client, visitor):
    """בלי זה הדפדפן מגיש את השורש מהמטמון והשער לא רץ בכלל."""
    assert visitor.get("/").headers["Cache-Control"] == "no-store"
    assert client.get("/").headers["Cache-Control"] == "no-store"


def test_the_door_tells_the_page_when_to_come_back(client):
    """הדף מקבל את הסף, כי יש חזרה לאפליקציה שלא מייצרת בקשה כלל."""
    html = client.get("/").get_data(as_text=True)
    assert f'data-splash-idle="{auth.SPLASH_IDLE_SECONDS}"' in html
    assert 'data-splash-url="/welcome"' in html


def test_a_screen_with_results_is_not_sent_back(client):
    """מסך עם תוצאות לא יקפוץ למסך פתיחה - המשתמש היה מאבד אותן."""
    html = client.post("/", data={"plate": "12345678", "query": "רפידות"}
                       ).get_data(as_text=True)
    assert "data-splash-idle" not in html


def test_the_welcome_screen_itself_is_not_activity(visitor):
    """טעינת מסך הפתיחה בלי ללחוץ לא מבריחה אותו."""
    visitor.get("/welcome")
    response = visitor.get("/")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/welcome")


def test_enter_ignores_external_targets(visitor):
    response = visitor.get("/enter?next=https://evil.example/x")
    assert response.headers["Location"] == "/"


def test_shared_result_link_skips_the_splash(visitor):
    """קישור עם מספר רישוי הוא תוצאה ששיתפו - אסור שיתאדה למסך פתיחה."""
    assert visitor.get("/?plate=12345678").status_code == 200


def test_search_post_is_never_gated(visitor):
    response = visitor.post("/", data={"plate": "12345678", "query": "רפידות קדמיות"})
    assert response.status_code == 200
    assert "TEST-001" in response.get_data(as_text=True)


def test_other_screens_open_directly(visitor):
    """קישור עמוק שנשלח לעובד נפתח במקום שאליו הוא מצביע."""
    for route in ["/parts", "/vehicles", "/login", "/signup", "/healthz"]:
        assert visitor.get(route).status_code == 200, route


def test_login_counts_as_entering(app, org):
    """אחרי התחברות נוחתים באפליקציה, לא חוזרים למכונית."""
    fresh = app.test_client()
    response = fresh.post("/login", data={"email": "owner@t.test",
                                          "password": "password123"})
    assert response.headers["Location"] == "/"
    assert fresh.get("/").status_code == 200
