"""ניהול משתמשים בארגון והזמנות."""
from datetime import datetime, timedelta, timezone

import pytest

from app.auth_models import Invitation, Organization, User
from app.models import db


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
            user = User(email=email, role=role, organization=organization)
            user.set_password("password123")
            db.session.add(user)
        db.session.commit()
        yield {"home": home.id, "other": other.id}


def login(client, email):
    return client.post("/login", data={"email": email, "password": "password123"})


# ---------- גישה למסך ----------

def test_only_owner_reaches_team_screen(client, team):
    for email, expected in [("owner@home.test", 200),
                            ("mgr@home.test", 403),
                            ("mech@home.test", 403)]:
        client.post("/logout")
        login(client, email)
        assert client.get("/team").status_code == expected, email


def test_anonymous_is_redirected(client, team):
    assert client.get("/team").status_code == 302


# ---------- הזמנות ----------

def test_owner_can_invite(client, app, team):
    login(client, "owner@home.test")
    client.post("/team/invite", data={"email": "new@home.test", "role": "manager"})
    with app.app_context():
        invitation = Invitation.query.filter_by(email="new@home.test").first()
        assert invitation is not None
        assert invitation.role == "manager"
        assert invitation.organization_id == team["home"]
        assert len(invitation.token) > 30      # טוקן ארוך ואקראי


def test_invite_rejects_existing_email(client, app, team):
    login(client, "owner@home.test")
    client.post("/team/invite", data={"email": "mgr@home.test", "role": "mechanic"})
    with app.app_context():
        assert Invitation.query.count() == 0


def test_accepting_invitation_creates_user_in_that_org(client, app, team):
    login(client, "owner@home.test")
    client.post("/team/invite", data={"email": "joiner@home.test", "role": "mechanic"})
    with app.app_context():
        token = Invitation.query.first().token
    client.post("/logout")

    client.post(f"/invite/{token}",
                data={"password": "longenough1", "password_confirm": "longenough1",
                      "full_name": "עובד חדש"})
    with app.app_context():
        user = User.query.filter_by(email="joiner@home.test").first()
        assert user is not None
        assert user.organization_id == team["home"]
        assert user.role == "mechanic"
        assert Invitation.query.first().accepted_at is not None


def test_invitation_cannot_be_reused(client, app, team):
    login(client, "owner@home.test")
    client.post("/team/invite", data={"email": "once@home.test", "role": "mechanic"})
    with app.app_context():
        token = Invitation.query.first().token
    client.post("/logout")
    client.post(f"/invite/{token}",
                data={"password": "longenough1", "password_confirm": "longenough1"})
    client.post("/logout")
    assert client.get(f"/invite/{token}").status_code == 404


def test_expired_invitation_is_rejected(client, app, team):
    with app.app_context():
        invitation = Invitation(
            email="late@home.test", role="mechanic", token="expired-token-value",
            organization_id=team["home"],
            expires_at=datetime.now(timezone.utc) - timedelta(days=1))
        db.session.add(invitation)
        db.session.commit()
    assert client.get("/invite/expired-token-value").status_code == 404


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
    assert "mgr@home.test" in html
    assert "owner@other.test" not in html


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
    assert client.get("/team").status_code == 302     # לא מחובר
