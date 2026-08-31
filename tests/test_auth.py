"""ארגונים, משתמשים והרשאות."""
from urllib.parse import unquote
import pytest

from app import auth
from app.auth_models import Organization, User
from app.models import db


OWNER = "0501110001"
MANAGER = "0501110002"
MECHANIC = "0501110003"
STRANGER = "0509999999"      # מספר תקין שאינו רשום לאיש


@pytest.fixture
def org(app):
    with app.app_context():
        organization = Organization(name="מוסך בדיקה", slug="test-garage")
        db.session.add(organization)
        db.session.flush()
        for phone, role in [(OWNER, "owner"),
                            (MANAGER, "manager"),
                            (MECHANIC, "mechanic")]:
            db.session.add(
                User(phone=phone, role=role, organization=organization)
            )
        db.session.commit()
        yield organization


def login(client, phone):
    """הזדהות כבעל המספר. יוצאים תחילה - הלקוח כבר מזוהה כברירת מחדל."""
    client.post("/logout")
    return client.post("/login", data={"phone": phone})


# ---------- הזהות ----------

@pytest.mark.parametrize("typed", [
    "0501110001", "050-111-0001", "050 111 0001",
    "+972501110001", "00972501110001", "972501110001",
])
def test_the_same_number_written_differently_is_the_same_person(client, org, typed):
    """מספר אחד, עשר צורות כתיבה. הבדיקה היא על המספר, לא על המחרוזת."""
    login(client, typed)
    assert client.get("/team").status_code == 200      # נכנס כבעלים


def test_a_user_has_no_password_to_lose(app, org):
    with app.app_context():
        user = User.query.filter_by(phone=OWNER).first()
        assert not hasattr(user, "password_hash")
        assert user.phone == OWNER


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
        assert User.query.filter_by(phone=OWNER).first().is_active is False


# ---------- אכיפה על נקודות הקצה ----------

def test_unidentified_cannot_write(visitor, org):
    assert visitor.post("/api/parts", json={"part_number": "X", "name_he": "y"}).status_code == 401
    assert visitor.delete("/api/parts/1").status_code == 401
    assert visitor.get("/parts/new").status_code == 302        # הפניה למסך ההזדהות
    assert visitor.post("/import").status_code == 302


def test_unidentified_cannot_read_either(visitor, org):
    """בלי הזדהות אין אפליקציה - גם לא הקטלוג."""
    for path in ["/parts", "/dashboard", "/export.csv"]:
        response = visitor.get(path)
        assert response.status_code == 302, path
        assert "/login" in response.headers["Location"], path
    for path in ["/api/parts", "/api/stats"]:
        assert visitor.get(path).status_code == 401, path


def test_the_door_itself_stays_open(visitor, org):
    """מה שצריך כדי להגיע לשדה ההזדהות - ולא יותר."""
    for path in ["/welcome", "/login", "/manifest.webmanifest", "/sw.js", "/healthz"]:
        assert visitor.get(path).status_code == 200, path


def test_mechanic_cannot_write_but_can_read(client, org):
    login(client, MECHANIC)
    assert client.post("/api/parts", json={"part_number": "M", "name_he": "y"}).status_code == 403
    assert client.get("/parts").status_code == 200


def test_manager_can_write(client, org):
    login(client, MANAGER)
    assert client.post(
        "/api/parts", json={"part_number": "MGR-1", "name_he": "רפידות"}
    ).status_code == 201


# ---------- הזדהות ----------

def test_an_unlisted_number_is_turned_away(client, org):
    response = login(client, STRANGER)
    assert "אינו מורשה" in response.get_data(as_text=True) or response.status_code == 200
    assert client.post(
        "/api/parts", json={"part_number": "X", "name_he": "y"}
    ).status_code == 401


def test_a_number_that_is_not_a_number_is_rejected(client, org):
    response = login(client, "לא מספר")
    assert "אינו תקין" in response.get_data(as_text=True)


def test_a_disabled_user_cannot_identify(client, app, org):
    with app.app_context():
        user = User.query.filter_by(phone=MECHANIC).first()
        user.active = False
        db.session.commit()
    response = login(client, MECHANIC)
    assert "מושבתים" in response.get_data(as_text=True)
    assert client.get("/parts").status_code == 302


def test_identifying_records_the_number_in_the_log(client, app, org):
    login(client, OWNER)
    with app.app_context():
        from app.activity import ActivityLog

        row = (
            ActivityLog.query.filter_by(action="auth.login")
            .order_by(ActivityLog.id.desc())
            .first()
        )
        assert row.user_label == OWNER


def test_logout_ends_the_session(client, org):
    login(client, MANAGER)
    assert client.post("/api/parts", json={"part_number": "L-1", "name_he": "x"}).status_code == 201
    client.post("/logout")
    assert client.post("/api/parts", json={"part_number": "L-2", "name_he": "x"}).status_code == 401


def test_login_redirect_ignores_external_next(client, org):
    """פרמטר next לא יכול להפנות לאתר חיצוני."""
    client.post("/logout")
    response = client.post("/login?next=https://evil.example/x", data={"phone": OWNER})
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
    assert 'id="enter-app" href="/enter?next=%2F"' in html


def test_welcome_honours_next_but_not_external_targets(client):
    html = client.get("/welcome?next=/parts").get_data(as_text=True)
    assert 'id="enter-app" href="/enter?next=%2Fparts"' in html

    html = client.get("/welcome?next=https://evil.example/x").get_data(as_text=True)
    assert "evil.example" not in html
    assert 'id="enter-app" href="/enter?next=%2F"' in html


# ---------- שער הכניסה ----------

def test_visitor_meets_the_car_before_the_app(visitor):
    """הדבר הראשון שרואים בפתיחת האפליקציה הוא המכונית."""
    response = visitor.get("/")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/welcome")


def test_the_car_leads_to_the_single_field(visitor):
    """מי שלא הזדהה - הלחיצה על המכונית מביאה אותו לשדה, לא לאפליקציה."""
    response = visitor.get("/enter?next=/parts")
    assert response.status_code == 302
    assert response.headers["Location"] == "/login?next=%2Fparts"


def test_the_gate_sends_a_link_that_actually_opens(visitor):
    """ההפניה להזדהות חייבת להיות כתובת שאפשר לבקש, לא רק להסתכל בה.

    בייצור היא לא הייתה: /parts החזיר הפניה ל-/login?next=/parts? -
    שני סימני שאלה באותה כתובת - ומי שנרמל אותה בדרך הפך אותה לנתיב
    /login%3Fnext=/parts, שאין לו מסלול. המכונאי ביקש להזדהות וקיבל
    404. הבדיקה הולכת בעקבות ההפניה עד הסוף.
    """
    response = visitor.get("/parts", headers={"Referer": "http://localhost/x"})
    assert response.status_code == 302

    location = response.headers["Location"]
    assert "?" not in location.split("?", 1)[1]
    assert visitor.get(location, headers={"Referer": "http://localhost/x"}).status_code == 200


def test_the_gate_keeps_the_query_of_the_page_it_stopped(visitor, org):
    """מי שנעצר על טבלה ממוינת ומסוננת חוזר אליה, לא לטבלה חשופה."""
    stopped = "/parts?sort=price_asc&q=בלם"
    location = visitor.get(
        stopped, headers={"Referer": "http://localhost/x"}
    ).headers["Location"]

    assert visitor.get(location, headers={"Referer": "http://localhost/x"}).status_code == 200
    landed = visitor.post(location, data={"phone": OWNER})
    # העברית חוזרת מקודדת, וזו אותה כתובת עצמה.
    assert unquote(landed.headers["Location"]) == stopped


def test_a_page_without_a_query_does_not_grow_one(visitor):
    """full_path מוסיף "?" לכל בקשה; היעד לא אמור לסחוב אותו."""
    location = visitor.get(
        "/parts", headers={"Referer": "http://localhost/x"}
    ).headers["Location"]
    assert location == "/login?next=%2Fparts"


def test_identifying_continues_to_where_the_car_pointed(visitor, org):
    """היעד שהלחיצה כיוונה אליו נשמר דרך ההזדהות."""
    visitor.get("/enter?next=/parts")
    response = visitor.post("/login?next=/parts", data={"phone": OWNER})
    assert response.headers["Location"] == "/parts"


def test_clicking_the_car_opens_the_app_and_does_not_ask_again(identified):
    enter = identified.get("/enter?next=/")
    assert enter.status_code == 302
    assert enter.headers["Location"] == "/"
    # מכאן והלאה נכנסים ישר לאפליקציה, בלי לעבור שוב במסך הפתיחה
    assert identified.get("/").status_code == 200


def test_navigating_inside_the_app_is_not_an_opening(identified):
    """הגעה ממסך אחר של האפליקציה אינה פתיחה שלה, ולא מנדנדת."""
    response = identified.get("/", headers={"Referer": "http://localhost/parts"})
    assert response.status_code == 200


def test_a_foreign_referrer_is_still_an_opening(identified):
    """קישור מאתר אחר הוא פתיחה של האפליקציה, לא ניווט בתוכה."""
    response = identified.get("/", headers={"Referer": "https://elsewhere.example/x"})
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/welcome")


def test_every_opening_meets_the_car_again(identified):
    """הלב של העניין: לא מדובר בזמן שעבר אלא בפתיחה מחדש."""
    identified.get("/enter?next=/")
    assert identified.get("/").status_code == 200          # הלחיצה נכנסה

    # פתיחה נוספת, שנייה אחרי - ושוב המכונית
    response = identified.get("/")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/welcome")


def test_the_entry_token_burns_after_one_use(identified):
    """האסימון פותח את הדלת בדיוק פעם אחת."""
    identified.get("/enter?next=/")
    assert identified.get("/").status_code == 200
    assert identified.get("/").status_code == 302


def test_clicking_the_car_never_loops(identified):
    """גם בלי Referer הלחיצה חייבת להיכנס, אחרת נוצרת לולאה."""
    enter = identified.get("/enter?next=/")
    landing = identified.get(enter.headers["Location"])
    assert landing.status_code == 200


def test_identifying_counts_as_entering(app, org):
    """אחרי הזדהות נוחתים באפליקציה, לא חוזרים למכונית."""
    fresh = app.test_client()
    response = fresh.post("/login", data={"phone": OWNER})
    assert response.headers["Location"] == "/"
    assert fresh.get("/").status_code == 200
