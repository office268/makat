"""מחיקת הקטלוג - פעולה הרסנית, ולכן גבולותיה מקובעים בבדיקות."""
from app.auth_models import Organization, User
from app.models import CrossReference, Fitment, OrgPart, Part, Supplier
from scripts.clear_catalog import clear_catalog


def test_clears_catalog_and_private_layers(app, org_id):
    with app.app_context():
        assert Part.query.count() == 1
        assert OrgPart.query.count() == 1

    clear_catalog(app)

    with app.app_context():
        assert Part.query.count() == 0
        assert OrgPart.query.count() == 0
        assert CrossReference.query.count() == 0
        assert Fitment.query.count() == 0
        assert Supplier.query.count() == 0


def test_keeps_organizations_and_users(app, org_id):
    """החשבונות שורדים - הם פשוט מתעוררים לקטלוג ריק."""
    with app.app_context():
        orgs_before = Organization.query.count()
        users_before = User.query.count()

    clear_catalog(app)

    with app.app_context():
        assert Organization.query.count() == orgs_before
        assert User.query.count() == users_before


def test_app_still_works_on_an_empty_catalog(app, client, org_id):
    """הכי חשוב: מסכים לא נשברים כשאין שום מק"ט."""
    clear_catalog(app)
    for path in ["/", "/parts", "/demo", "/api/parts", "/api/stats", "/export.csv"]:
        assert client.get(path).status_code == 200, path
    assert client.get("/api/stats").get_json()["parts"] == 0


def test_reset_catalog_env_var_is_actually_wired(app, monkeypatch):
    """RESET_CATALOG=1 חייב להגיע למחיקה בפועל.

    הבדיקה קיימת כי טלאי קודם ל-init_db הסתמך על שורה שכבר לא הייתה
    בקובץ, עבר בשקט בלי לעשות דבר, והמשתנה נשאר מחובר לכלום בפרודקשן.
    """
    import scripts.init_db as init_db
    from app.models import Part

    monkeypatch.setenv("RESET_CATALOG", "1")
    monkeypatch.setenv("SEED_DEMO", "0")
    monkeypatch.setattr(init_db, "create_app", lambda: app)
    monkeypatch.setattr(init_db, "upgrade", lambda *a, **k: None)
    monkeypatch.setattr(init_db, "_adopt_pre_alembic_database", lambda: False)

    with app.app_context():
        assert Part.query.count() == 1

    init_db.main()

    with app.app_context():
        assert Part.query.count() == 0


def test_without_the_env_var_nothing_is_deleted(app, monkeypatch):
    import scripts.init_db as init_db
    from app.models import Part

    monkeypatch.delenv("RESET_CATALOG", raising=False)
    monkeypatch.setenv("SEED_DEMO", "0")
    monkeypatch.setattr(init_db, "create_app", lambda: app)
    monkeypatch.setattr(init_db, "upgrade", lambda *a, **k: None)
    monkeypatch.setattr(init_db, "_adopt_pre_alembic_database", lambda: False)

    init_db.main()

    with app.app_context():
        assert Part.query.count() == 1
