"""נעילת הכתיבה - מתג חירום שחוסם שינוי נתונים גם למשתמש מורשה.

מאז שנכנסו ההרשאות זו שכבה שנייה: משתמש עם תפקיד מנהל עובר את
בדיקת ההרשאות, ועדיין נחסם כשהמערכת במצב קריאה בלבד.
"""
import pytest

from app import create_app
from app.auth_models import Organization, User
from app.config import TestConfig
from app.models import Part, db


class ReadOnlyConfig(TestConfig):
    READ_ONLY = True


@pytest.fixture
def ro_client():
    app = create_app(ReadOnlyConfig)
    with app.app_context():
        db.drop_all()
        db.create_all()
        db.session.add(Part(part_number="RO-1", name_he="חלק"))
        organization = Organization(name="מוסך", slug="ro-garage")
        db.session.add(organization)
        db.session.flush()
        manager = User(phone="0505550001", email="mgr@ro.test", role="manager",
                       organization=organization)
        db.session.add(manager)
        db.session.commit()
        part_id = Part.query.first().id

        client = app.test_client()
        # מזדהים כמנהל - כך שכל חסימה שנראה מגיעה מהנעילה, לא מההרשאות
        client.post("/login", data={"phone": "0505550001"})
        yield client, part_id
        db.session.remove()
        db.drop_all()


def test_api_writes_are_blocked(ro_client):
    client, part_id = ro_client
    assert client.post("/api/parts", json={"part_number": "X", "name_he": "y"}).status_code == 403
    assert client.put(f"/api/parts/{part_id}", json={"price": 1}).status_code == 403
    assert client.delete(f"/api/parts/{part_id}").status_code == 403


def test_web_writes_are_blocked(ro_client):
    client, part_id = ro_client
    for path in [f"/parts/{part_id}/delete", "/parts/new", "/import"]:
        assert client.post(path).status_code == 302, path
    # ולא נמחק דבר
    assert client.get(f"/parts/{part_id}").status_code == 200


def test_reads_still_work(ro_client):
    client, part_id = ro_client
    for path in ["/", "/parts", "/dashboard", "/api/parts", "/api/stats", "/export.csv",
                 f"/parts/{part_id}", "/parts/new"]:
        assert client.get(path).status_code == 200, path


def test_demo_search_still_works(ro_client):
    """POST /demo הוא חיפוש, לא שינוי - חייב להמשיך לעבוד."""
    client, _ = ro_client
    response = client.post("/", data={"plate": "12345678", "query": "רפידות קדמיות"})
    assert response.status_code == 200
    assert "COROLLA" in response.get_data(as_text=True)


def test_writes_work_when_guard_is_off(client, app):
    """כשהנעילה כבויה, מנהל מחובר יכול לכתוב כרגיל."""
    with app.app_context():
        organization = Organization(name="מוסך", slug="open-garage")
        db.session.add(organization)
        db.session.flush()
        manager = User(phone="0505550002", email="mgr@open.test", role="manager",
                       organization=organization)
        db.session.add(manager)
        db.session.commit()
    client.post("/logout")
    client.post("/login", data={"phone": "0505550002"})
    assert client.post(
        "/api/parts", json={"part_number": "OPEN-1", "name_he": "חלק"}
    ).status_code == 201


def test_admin_discovery_writes_are_blocked(ro_client):
    """הנעילה רצה לפני בדיקת ההרשאות, ולכן 302 כאן מוכיח שהיא תפסה.

    משתמש שאינו superadmin היה מקבל 403 מהמסך עצמו; ההפניה מגיעה
    מהנעילה בלבד.
    """
    client, _ = ro_client
    for path in ["/admin/discovery/start", "/admin/discovery/step",
                 "/admin/discovery/verify", "/admin/discovery/delete"]:
        assert client.post(path).status_code == 302, path


def test_a_managed_platform_is_not_born_locked(monkeypatch):
    """‏READ_ONLY כבוי כברירת מחדל, גם על Railway.

    הוא נולד דלוק שם כשלא הייתה מערכת הרשאות. מאז יש אחת, ודלוק
    כברירת מחדל פירושו שכל סביבה חדשה נולדת נעולה - מי שמקים staging
    מגלה שאי אפשר להזין מק"ט, ומקבל הודעה שמפנה אותו למערכת שכבר עובדת.
    """
    import importlib

    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    monkeypatch.delenv("READ_ONLY", raising=False)

    from app import config as config_module

    fresh = importlib.reload(config_module)
    try:
        assert fresh.Config.IS_MANAGED_PLATFORM is True, "אנחנו אכן על פלטפורמה מנוהלת"
        assert fresh.Config.READ_ONLY is False

        # ועדיין ניתן להדלקה במפורש - זה כל תפקידו
        monkeypatch.setenv("READ_ONLY", "1")
        assert importlib.reload(config_module).Config.READ_ONLY is True
    finally:
        monkeypatch.delenv("READ_ONLY", raising=False)
        monkeypatch.delenv("RAILWAY_ENVIRONMENT_NAME", raising=False)
        importlib.reload(config_module)


def test_the_read_only_message_no_longer_promises_a_shipped_feature():
    """ההודעה אמרה "ייפתח עם הפעלת מערכת המשתמשים וההרשאות". היא פעילה."""
    from app.guards import MESSAGE

    assert "מערכת המשתמשים" not in MESSAGE
    assert "חירום" in MESSAGE
