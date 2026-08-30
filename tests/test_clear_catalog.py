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
    for path in ["/", "/parts", "/dashboard", "/api/parts", "/api/stats", "/export.csv"]:
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
    monkeypatch.setattr(init_db, "create_app", lambda: app)
    monkeypatch.setattr(init_db, "upgrade", lambda *a, **k: None)
    monkeypatch.setattr(init_db, "_adopt_pre_alembic_database", lambda: False)

    init_db.main()

    with app.app_context():
        assert Part.query.count() == 1


def test_a_missing_catalog_file_stops_the_deploy(app, monkeypatch, tmp_path):
    """נתיב CSV שגוי חייב להפיל את הדיפלוי, לא לעבור בשקט.

    לפני התיקון init_db התעלם מהערך המוחזר של load: שינוי שם הקובץ בריפו
    בלי עדכון המשתנה בפרודקשן היה מייצר דיפלוי ירוק שבו הקטלוג פשוט לא
    מתעדכן, והקטלוג הישן ב-DB ממשיך להיענות כאילו הכל תקין.
    """
    import scripts.init_db as init_db

    monkeypatch.delenv("RESET_CATALOG", raising=False)
    monkeypatch.setenv("IMPORT_PARTS_CSV", str(tmp_path / "אין-כזה.csv"))
    monkeypatch.setattr(init_db, "create_app", lambda: app)
    monkeypatch.setattr(init_db, "upgrade", lambda *a, **k: None)
    monkeypatch.setattr(init_db, "_adopt_pre_alembic_database", lambda: False)

    assert init_db.main() == 1


def test_the_real_catalog_file_loads_and_the_deploy_passes(app, monkeypatch):
    """המקבילה החיובית: הנתיב שבריפו באמת נטען ומחזיר 0.

    זו הבדיקה שתיפול אם מישהו ישנה שוב את שם הקובץ בלי לעדכן את מי
    שמצביע עליו.
    """
    import pathlib

    import scripts.init_db as init_db

    csv = pathlib.Path(__file__).resolve().parent.parent / "data" / "parts_catalog.csv"
    assert csv.exists()

    monkeypatch.delenv("RESET_CATALOG", raising=False)
    monkeypatch.setenv("IMPORT_PARTS_CSV", str(csv))
    monkeypatch.setattr(init_db, "create_app", lambda: app)
    monkeypatch.setattr(init_db, "upgrade", lambda *a, **k: None)
    monkeypatch.setattr(init_db, "_adopt_pre_alembic_database", lambda: False)

    assert init_db.main() == 0
