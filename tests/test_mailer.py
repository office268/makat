"""שליחת דוא"ל, ובעיקר: מה קורה כשהיא לא מוגדרת או נכשלת."""
import pytest

from app import mailer
from app.auth_models import Invitation, Organization, User
from app.models import db


@pytest.fixture
def owner(app):
    with app.app_context():
        organization = Organization(name="מוסך", slug="mail-org")
        db.session.add(organization)
        db.session.flush()
        user = User(email="owner@mail.test", role="owner", organization=organization)
        user.set_password("password123")
        db.session.add(user)
        db.session.commit()
        yield user.id


def test_not_configured_without_env(app, monkeypatch):
    monkeypatch.delenv("MAIL_SERVER", raising=False)
    monkeypatch.delenv("MAIL_SENDER", raising=False)
    with app.app_context():
        assert mailer.is_configured() is False


def test_configured_with_env(app, monkeypatch):
    monkeypatch.setenv("MAIL_SERVER", "smtp.example.com")
    monkeypatch.setenv("MAIL_SENDER", "noreply@example.com")
    with app.app_context():
        assert mailer.is_configured() is True


def test_unconfigured_send_logs_and_reports_false(app, monkeypatch):
    """בלי הגדרה ההודעה נרשמת ללוג ומדווחת ככישלון - לא מעמידים פנים."""
    monkeypatch.delenv("MAIL_SERVER", raising=False)
    with app.app_context():
        assert mailer.send("a@b.test", "נושא", "גוף") is False


def test_send_failure_does_not_raise(app, monkeypatch):
    """כשל SMTP לא אמור להפיל את הפעולה שיצרה את ההודעה."""
    monkeypatch.setenv("MAIL_SERVER", "smtp.invalid.example")
    monkeypatch.setenv("MAIL_SENDER", "noreply@example.com")
    monkeypatch.setenv("MAIL_PORT", "1")
    with app.app_context():
        assert mailer.send("a@b.test", "נושא", "גוף") is False


def test_invitation_is_created_even_when_mail_fails(client, app, owner, monkeypatch):
    """הכי חשוב: אם הדוא\"ל לא יצא, ההזמנה עדיין קיימת והקישור זמין."""
    monkeypatch.delenv("MAIL_SERVER", raising=False)
    client.post("/login", data={"email": "owner@mail.test", "password": "password123"})
    response = client.post(
        "/team/invite", data={"email": "new@mail.test", "role": "mechanic"},
        follow_redirects=True)

    with app.app_context():
        invitation = Invitation.query.filter_by(email="new@mail.test").first()
        assert invitation is not None
    html = response.get_data(as_text=True)
    assert "אינה מוגדרת" in html          # המסך אומר את האמת
    assert invitation.token in html       # והקישור זמין להעתקה


def test_invitation_email_contains_the_link(app, owner, monkeypatch):
    sent = {}

    def fake_send(to, subject, body):
        sent.update(to=to, subject=subject, body=body)
        return True

    monkeypatch.setattr(mailer, "send", fake_send)
    with app.app_context():
        organization = Organization.query.filter_by(slug="mail-org").first()
        inviter = User.query.filter_by(email="owner@mail.test").first()
        invitation = Invitation(
            email="target@mail.test", role="manager", token="tok123",
            organization_id=organization.id,
            expires_at=__import__("datetime").datetime(2030, 1, 1))
        db.session.add(invitation)
        db.session.commit()
        mailer.send_invitation(invitation, "https://app.test/invite/tok123", inviter)

    assert sent["to"] == "target@mail.test"
    assert "https://app.test/invite/tok123" in sent["body"]
    assert "מנהל" in sent["body"]
