"""ניהול המורשים של הארגון: מי יכול להיכנס, באיזה תפקיד."""
import pytest

from app.auth_models import Organization, User
from app.models import db

# לכל משתמש בבדיקות מספר משלו - הוא הזהות שאיתה נכנסים
PHONES = {
    "owner@home.test": "0503330001",
    "mgr@home.test": "0503330002",
    "mech@home.test": "0503330003",
    "owner@other.test": "0503330004",
}


@pytest.fixture
def team(app):
    """ארגון עם בעלים, מנהל ומכונאי - וארגון שני זר לו."""
    with app.app_context():
        home = Organization(name="מוסך הבית", slug="home")
        other = Organization(name="מוסך זר", slug="other")
        db.session.add_all([home, other])
        db.session.flush()

        for email, role, organization in [
            ("owner@home.test", "owner", home),
            ("mgr@home.test", "manager", home),
            ("mech@home.test", "mechanic", home),
            ("owner@other.test", "owner", other),
        ]:
            db.session.add(
                User(phone=PHONES[email], email=email, role=role,
                     organization=organization)
            )
        db.session.commit()
        yield {"home": home.id, "other": other.id}


def login(client, email):
    """הזדהות כמישהו אחר. הבדיקות מזהות אותו בדוא"ל, המערכת במספר."""
    client.post("/logout")
    return client.post("/login", data={"phone": PHONES[email]})


# ---------- גישה למסך ----------

def test_only_owner_reaches_team_screen(client, team):
    for email, expected in [("owner@home.test", 200),
                            ("mgr@home.test", 403),
                            ("mech@home.test", 403)]:
        login(client, email)
        assert client.get("/team").status_code == expected, email


def test_the_unidentified_are_redirected(visitor, team):
    assert visitor.get("/team").status_code == 302


# ---------- הוספת מורשה ----------

def test_owner_can_add_a_phone(client, app, team):
    login(client, "owner@home.test")
    client.post("/team/add", data={"phone": "050-333-9001", "role": "manager",
                                   "full_name": "עובד חדש"})
    with app.app_context():
        user = User.query.filter_by(phone="0503339001").first()
        assert user is not None
        assert user.role == "manager"
        assert user.full_name == "עובד חדש"
        assert user.organization_id == team["home"]


def test_an_added_phone_can_enter_immediately(client, app, team):
    """אין הזמנה לשלוח ואין סיסמה לקבוע - המספר הוא כל מה שצריך."""
    login(client, "owner@home.test")
    client.post("/team/add", data={"phone": "0503339002", "role": "mechanic"})
    client.post("/logout")

    client.post("/login", data={"phone": "0503339002"})
    assert client.get("/parts").status_code == 200


def test_add_rejects_a_malformed_number(client, app, team):
    login(client, "owner@home.test")
    before = None
    with app.app_context():
        before = User.query.count()
    client.post("/team/add", data={"phone": "12", "role": "mechanic"})
    with app.app_context():
        assert User.query.count() == before


def test_add_rejects_a_number_already_registered(client, app, team):
    login(client, "owner@home.test")
    client.post("/team/add", data={"phone": PHONES["mgr@home.test"],
                                   "role": "mechanic"})
    with app.app_context():
        assert User.query.filter_by(phone=PHONES["mgr@home.test"]).count() == 1
        assert User.query.filter_by(phone=PHONES["mgr@home.test"]).first().role == "manager"


def test_the_number_is_stored_normalized(client, app, team):
    """אותו אדם לא ייחסם כי בפעם הבאה הוא יקליד בלי מקפים."""
    login(client, "owner@home.test")
    client.post("/team/add", data={"phone": "+972 50 333 9003", "role": "mechanic"})
    with app.app_context():
        assert User.query.filter_by(phone="0503339003").first() is not None


def test_only_owner_can_add(client, app, team):
    login(client, "mgr@home.test")
    assert client.post(
        "/team/add", data={"phone": "0503339004", "role": "mechanic"}
    ).status_code == 403
    with app.app_context():
        assert User.query.filter_by(phone="0503339004").first() is None


# ---------- גבולות בין ארגונים ----------

def test_owner_cannot_touch_another_orgs_user(client, app, team):
    with app.app_context():
        stranger_id = User.query.filter_by(email="owner@other.test").first().id
    login(client, "owner@home.test")
    assert client.post(f"/team/{stranger_id}/role", data={"role": "mechanic"}).status_code == 404
    assert client.post(f"/team/{stranger_id}/toggle").status_code == 404
    with app.app_context():
        assert User.query.filter_by(email="owner@other.test").first().role == "owner"


def test_team_screen_lists_only_own_organization(client, team):
    login(client, "owner@home.test")
    html = client.get("/team").get_data(as_text=True)
    assert "050-333-0002" in html                      # המנהל של אותו מוסך
    assert "050-333-0004" not in html                  # הבעלים של המוסך הזר


# ---------- שמירה על בעלים ----------

def test_cannot_demote_the_last_owner(client, app, team):
    with app.app_context():
        owner_id = User.query.filter_by(email="owner@home.test").first().id
    login(client, "owner@home.test")
    client.post(f"/team/{owner_id}/role", data={"role": "mechanic"})
    with app.app_context():
        assert User.query.filter_by(email="owner@home.test").first().role == "owner"


def test_cannot_disable_yourself(client, app, team):
    with app.app_context():
        owner_id = User.query.filter_by(email="owner@home.test").first().id
    login(client, "owner@home.test")
    client.post(f"/team/{owner_id}/toggle")
    with app.app_context():
        assert User.query.filter_by(email="owner@home.test").first().active is True


def test_owner_can_change_another_users_role(client, app, team):
    with app.app_context():
        mech_id = User.query.filter_by(email="mech@home.test").first().id
    login(client, "owner@home.test")
    client.post(f"/team/{mech_id}/role", data={"role": "manager"})
    with app.app_context():
        assert User.query.filter_by(email="mech@home.test").first().role == "manager"


def test_disabled_user_cannot_log_in(client, app, team):
    with app.app_context():
        mech_id = User.query.filter_by(email="mech@home.test").first().id
    login(client, "owner@home.test")
    client.post(f"/team/{mech_id}/toggle")
    client.post("/logout")

    login(client, "mech@home.test")
    assert client.get("/team").status_code == 302     # לא הזדהה
