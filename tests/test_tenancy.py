"""הפרדת נתונים בין ארגונים.

הקטלוג משותף; מחיר, מלאי וספקים פרטיים. הבדיקות כאן מוודאות שהגבול
הזה נאכף בכל נקודת יציאה - מסכים, API וייצוא CSV. דליפה כאן פירושה
שמוסך אחד רואה את המחירים של מוסך אחר.
"""
import pytest

from app.auth_models import Organization, User
from app.models import OrgPart, Part, Supplier, db


@pytest.fixture
def two_orgs(app):
    """שני ארגונים שמתמחרים את אותו מק"ט מהקטלוג המשותף."""
    with app.app_context():
        part = Part.query.filter_by(part_number="TEST-001").first()

        created = {}
        for slug, price, stock, supplier_name in [
            ("alpha", 300.0, 9, "ספק של אלפא"),
            ("beta", 450.0, 1, "ספק של ביתא"),
        ]:
            organization = Organization(name=slug, slug=slug)
            db.session.add(organization)
            db.session.flush()
            user = User(email=f"u@{slug}.test", role="manager", organization=organization)
            user.set_password("password123")
            db.session.add_all([
                user,
                OrgPart(organization=organization, part=part, price=price,
                        cost=price / 2, stock_qty=stock, min_stock=2,
                        location=f"{slug}-1"),
                Supplier(organization=organization, name=supplier_name),
            ])
            created[slug] = organization.id
        db.session.commit()
        yield created


def login(client, slug):
    return client.post("/login", data={"email": f"u@{slug}.test", "password": "password123"})


# ---------- הקטלוג משותף ----------

def test_catalog_identity_is_shared(client, two_orgs):
    """שני הארגונים רואים את אותו מק"ט מהקטלוג."""
    for slug in ("alpha", "beta"):
        client.post("/logout")
        login(client, slug)
        assert "TEST-001" in client.get("/parts").get_data(as_text=True)


# ---------- המחיר פרטי ----------

def test_each_org_sees_only_its_own_price(client, two_orgs):
    login(client, "alpha")
    html = client.get("/parts").get_data(as_text=True)
    assert "354.00" in html          # 300 * 1.18
    assert "531.00" not in html      # המחיר של ביתא

    client.post("/logout")
    login(client, "beta")
    html = client.get("/parts").get_data(as_text=True)
    assert "531.00" in html          # 450 * 1.18
    assert "354.00" not in html


def test_api_does_not_leak_other_org_pricing(client, two_orgs):
    login(client, "alpha")
    payload = client.get("/api/parts").get_json()
    item = next(i for i in payload["items"] if i["part_number"] == "TEST-001")
    assert item["price"] == 300.0
    assert item["stock_qty"] == 9


def test_anonymous_sees_catalog_without_prices(client, two_orgs):
    payload = client.get("/api/parts").get_json()
    item = next(i for i in payload["items"] if i["part_number"] == "TEST-001")
    assert item["part_number"] == "TEST-001"     # הקטלוג גלוי
    assert "price" not in item                   # המחיר לא
    assert "stock_qty" not in item


def test_export_csv_only_carries_own_pricing(client, two_orgs):
    login(client, "beta")
    text = client.get("/export.csv").get_data(as_text=True)
    assert "450" in text
    assert "300" not in text


def test_anonymous_export_has_no_prices(client, two_orgs):
    text = client.get("/export.csv").get_data(as_text=True)
    assert "TEST-001" in text
    assert "300" not in text and "450" not in text


# ---------- ספקים פרטיים ----------

def test_suppliers_are_scoped_to_the_organization(client, two_orgs):
    login(client, "alpha")
    html = client.get("/suppliers").get_data(as_text=True)
    assert "ספק של אלפא" in html
    assert "ספק של ביתא" not in html


# ---------- כתיבה נשארת בתוך הארגון ----------

def test_writing_price_creates_overlay_for_own_org_only(client, app, two_orgs):
    login(client, "alpha")
    part = Part.query.filter_by(part_number="TEST-001").first() if False else None
    with app.app_context():
        part_id = Part.query.filter_by(part_number="TEST-001").first().id
    client.put(f"/api/parts/{part_id}", json={"price": 999.0})

    with app.app_context():
        alpha = OrgPart.query.filter_by(
            organization_id=two_orgs["alpha"], part_id=part_id).first()
        beta = OrgPart.query.filter_by(
            organization_id=two_orgs["beta"], part_id=part_id).first()
        assert alpha.price == 999.0     # השתנה
        assert beta.price == 450.0      # לא נגעו בו


def test_stats_are_scoped(client, two_orgs):
    login(client, "alpha")
    alpha_stats = client.get("/api/stats").get_json()
    client.post("/logout")
    login(client, "beta")
    beta_stats = client.get("/api/stats").get_json()

    assert alpha_stats["parts"] == beta_stats["parts"]        # קטלוג משותף
    assert alpha_stats["in_stock"] == 1 and beta_stats["in_stock"] == 1
    assert alpha_stats["stock_value"] != beta_stats["stock_value"]  # מלאי פרטי
