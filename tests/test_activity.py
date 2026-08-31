"""לוג השימוש - מה נרשם, למי מותר לראות אותו, ומה לא נכנס אליו."""
from datetime import datetime, timedelta, timezone

import pytest

from app.activity import ActivityLog, prune
from app.auth_models import Organization, User
from app.models import db

# לכל משתמש בבדיקות מספר משלו - הוא הזהות שאיתה נכנסים
PHONES = {
    "owner@home.test": "0502220001",
    "mgr@home.test": "0502220002",
    "mech@home.test": "0502220003",
    "owner@other.test": "0502220004",
}


@pytest.fixture
def team(app):
    """בעלים, מנהל ומכונאי בארגון אחד - ובעלים של ארגון זר."""
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
            user = User(phone=PHONES[email], email=email, role=role,
                        organization=organization)
            db.session.add(user)
        db.session.commit()
        yield {"home": home.id, "other": other.id}


def login(client, email):
    """הזדהות כמישהו אחר. הבדיקות מזהות אותו בדוא"ל, המערכת במספר.

    היציאה תחילה אינה קישוט: הלקוח של הבדיקות כבר מזוהה, ומסך
    ההזדהות מחזיר את מי שכבר נכנס חזרה לאפליקציה.
    """
    client.post("/logout")
    return client.post("/login", data={"phone": PHONES[email]})


def entries(**filters):
    query = ActivityLog.query
    for field, value in filters.items():
        query = query.filter(getattr(ActivityLog, field) == value)
    return query.order_by(ActivityLog.id).all()


# ---------- מה נרשם אוטומטית ----------

def test_page_view_is_recorded(client, app):
    client.get("/parts")
    with app.app_context():
        row = entries(action="web.parts_list")[-1]
        assert row.method == "GET"
        assert row.path == "/parts"
        assert row.status_code == 200
        assert row.duration_ms is not None


def test_static_and_health_are_not_recorded(client, app):
    client.get("/healthz")
    client.get("/manifest.webmanifest")
    with app.app_context():
        assert entries(action="healthz") == []
        assert entries(action="pwa.manifest") == []


def test_logged_in_user_is_attributed(auth_client, app):
    auth_client.get("/dashboard")
    with app.app_context():
        row = entries(action="web.dashboard")[-1]
        assert row.user_label == "0500000001"
        assert row.user_role == "manager"
        assert row.organization_id is not None


def test_unidentified_visit_has_no_user(visitor, app):
    visitor.get("/categories")  # נעצר בשער ומופנה להזדהות
    with app.app_context():
        row = entries(action="web.categories")[-1]
        assert row.user_id is None
        assert row.organization_id is None
        assert row.actor == "אנונימי"


def test_error_status_is_recorded(client, app):
    client.get("/parts/99999")
    with app.app_context():
        row = ActivityLog.query.order_by(ActivityLog.id.desc()).first()
        assert row.status_code == 404
        assert row.is_error


def test_query_string_is_kept_and_form_values_are_not(client, app):
    client.get("/parts?q=TEST-001")
    with app.app_context():
        search = entries(action="web.parts_list")[-1]
        assert search.details_dict["args"]["q"] == "TEST-001"

        # מהטופס נשמרים שמות השדות בלבד. המספר עצמו נרשם פעם אחת,
        # בשורת הכניסה, כי זה מה שהלוג בא לספר.
        login_row = entries(action="auth.login")[-1]
        assert login_row.details_dict["form_fields"] == ["phone"]
        assert login_row.user_label == "0500000001"


# ---------- מה שהמסכים מוסיפים ----------

def test_login_and_failure_get_their_own_actions(client, app):
    client.post("/logout")
    client.post("/login", data={"phone": "0509999999"})   # מספר שאינו מורשה
    client.post("/login", data={"phone": "0500000001"})
    with app.app_context():
        failed = entries(action="auth.login_failed")[-1]
        assert failed.summary == "0509999999"
        assert failed.details_dict["reason"] == "unknown_phone"
        assert entries(action="auth.login")[-1].user_label == "0500000001"


def test_logout_stays_attributed_to_the_user_who_left(auth_client, app):
    auth_client.post("/logout")
    with app.app_context():
        row = entries(action="auth.logout")[-1]
        assert row.user_label == "0500000001"
        assert row.organization_id is not None


def test_part_view_records_the_part(auth_client, app):
    with app.app_context():
        from app.models import Part

        part_id = Part.query.filter_by(part_number="TEST-001").first().id
    auth_client.get(f"/parts/{part_id}")
    with app.app_context():
        row = entries(action="web.part_detail")[-1]
        assert row.entity_type == "part"
        assert row.entity_id == part_id
        assert "TEST-001" in row.summary


def test_part_creation_is_recorded(auth_client, app):
    auth_client.post(
        "/parts/new", data={"part_number": "LOG-1", "name_he": "חלק ללוג"}
    )
    with app.app_context():
        row = entries(action="web.part_create")[-1]
        assert row.details_dict["part_number"] == "LOG-1"
        assert row.is_write


def test_search_records_result_count(client, app):
    client.get("/parts?q=TEST-001")
    with app.app_context():
        row = entries(action="web.parts_list")[-1]
        assert row.details_dict["results"] == 1


# ---------- המסך ----------

def test_only_owner_reaches_the_log(client, team):
    for email, expected in [("owner@home.test", 200),
                            ("mgr@home.test", 403),
                            ("mech@home.test", 403)]:
        login(client, email)
        assert client.get("/activity").status_code == expected, email


def test_the_unidentified_are_redirected(visitor, team):
    assert visitor.get("/activity").status_code == 302


def test_owner_sees_only_own_organization(client, app, team):
    login(client, "owner@other.test")
    client.get("/dashboard")  # אירוע של הארגון הזר

    login(client, "owner@home.test")
    response = client.get("/activity")
    assert response.status_code == 200
    assert PHONES["owner@other.test"] not in response.get_data(as_text=True)


def test_filters_narrow_the_list(client, app, team):
    login(client, "owner@home.test")
    client.get("/dashboard")
    client.get("/categories")

    # הייצוא מראה את השורות עצמן, בלי הניווט שמופיע בכל דף
    rows = client.get("/activity.csv?action=web.dashboard").get_data(as_text=True)
    assert "web.dashboard" in rows
    assert "web.categories" not in rows


def test_errors_filter(client, app, team):
    login(client, "owner@home.test")
    client.get("/parts/99999")
    page = client.get("/activity?only=errors").get_data(as_text=True)
    assert "/parts/99999" in page
    assert "/parts/99999" not in client.get(
        "/activity?only=writes"
    ).get_data(as_text=True)


def test_csv_export(client, team):
    login(client, "owner@home.test")
    client.get("/dashboard")
    response = client.get("/activity.csv")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "created_at,user_label" in body
    assert "web.dashboard" in body


def test_single_entry_json_is_scoped_to_the_organization(client, app, team):
    login(client, "owner@other.test")
    client.get("/dashboard")
    with app.app_context():
        foreign_id = ActivityLog.query.filter_by(action="web.dashboard").first().id
    login(client, "owner@home.test")
    assert client.get(f"/activity/{foreign_id}").status_code == 404

    client.get("/categories")
    with app.app_context():
        mine = (
            ActivityLog.query.filter_by(action="web.categories")
            .order_by(ActivityLog.id.desc())
            .first()
            .id
        )
    payload = client.get(f"/activity/{mine}").get_json()
    assert payload["action"] == "web.categories"
    assert payload["label"] == "קטגוריות"


# ---------- תחזוקה וכיבוי ----------

def test_prune_removes_old_entries(app, client):
    client.get("/categories")
    with app.app_context():
        old = ActivityLog(
            created_at=datetime.now(timezone.utc) - timedelta(days=120),
            action="web.categories",
        )
        db.session.add(old)
        db.session.commit()
        old_id = old.id

        assert prune(90) == 1
        assert ActivityLog.query.filter_by(id=old_id).first() is None
        assert ActivityLog.query.count() >= 1


def test_logging_can_be_switched_off(app, client):
    app.config["ACTIVITY_LOG_ENABLED"] = False
    try:
        client.get("/categories")
        with app.app_context():
            assert entries(action="web.categories") == []
    finally:
        app.config["ACTIVITY_LOG_ENABLED"] = True


def test_superadmin_can_widen_to_all_organizations(client, app, team):
    app.config["SUPERADMIN_EMAILS"] = frozenset({"owner@home.test"})
    try:
        login(client, "owner@other.test")
        client.get("/dashboard")
        client.post("/logout")

        login(client, "owner@home.test")
        assert PHONES["owner@other.test"] not in client.get(
            "/activity"
        ).get_data(as_text=True)
        widened = client.get("/activity?scope=all").get_data(as_text=True)
        assert PHONES["owner@other.test"] in widened
    finally:
        app.config["SUPERADMIN_EMAILS"] = frozenset()
