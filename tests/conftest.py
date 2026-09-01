import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app  # noqa: E402
from app.auth_models import Organization, User  # noqa: E402
from app.config import TestConfig  # noqa: E402
from app.models import CrossReference, Fitment, OrgPart, Part, db  # noqa: E402
from app.services import get_or_create_category, get_or_create_manufacturer  # noqa: E402


# הזהות של משתמש ה-fixture. ההזדהות היא מספר טלפון, ולכן זה מה
# שהבדיקות מזינות.
FIXTURE_PHONE = "0500000001"


def _build_app():
    """בונה אפליקציה עם הקטלוג המשותף. מוחזר כגנרטור כדי ששני
    ה-fixtures - זה שלכל בדיקה וזה שלכל קובץ - יחלקו את אותו קוד."""
    application = create_app(TestConfig)
    with application.app_context():
        db.drop_all()
        db.create_all()

        # הקטלוג המשותף
        part = Part(
            part_number="TEST-001",
            name_he="רפידות בלם קדמיות COROLLA",
            part_type="brake_pads_front",
            manufacturer=get_or_create_manufacturer("TRW"),
            category=get_or_create_category("בלמים"),
        )
        part.cross_refs = [
            CrossReference(ref_number="04465-02220", ref_type="OEM", ref_brand="Toyota")
        ]
        part.fitments = [
            Fitment(make="טויוטה", model="COROLLA", year_from=2013, year_to=2018,
                    engine_code="1ZR-FE")
        ]
        db.session.add(part)

        # ארגון ברירת מחדל + השכבה הפרטית שלו על אותו מק"ט
        organization = Organization(name="ארגון בדיקה", slug="fixture-org")
        db.session.add(organization)
        db.session.flush()
        db.session.add(
            OrgPart(
                organization=organization, part=part,
                price=200.0, cost=140.0, stock_qty=4, min_stock=2, location="A-01",
            )
        )
        user = User(
            phone=FIXTURE_PHONE, email="fixture@t.test", role="manager",
            organization=organization,
        )
        db.session.add(user)
        db.session.commit()

        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture(autouse=True)
def offline_registry(monkeypatch):
    """אף בדיקה לא יוצאת לרשת.

    מרשם הרכב נשאל היום בכמה מאגרים כפול כמה אסטרטגיות, וכל בקשה
    שיוצאת באמת מחכה ל-timeout. בדיקה שתלויה בכך שאין רשת גם משנה
    התנהגות ברגע שיש - "המאגר לא נגיש" מול "אין רכב כזה" הן שתי
    תשובות נכונות ושונות. ברירת המחדל כאן היא מאגר שעונה ואין בו
    כלום; מי שצריך אחרת מחליף בעצמו.
    """
    from app import vehicles

    monkeypatch.setattr(vehicles, "_query", lambda resource_id, params: ([], None))


@pytest.fixture
def app():
    yield from _build_app()


@pytest.fixture(scope="module")
def shared_app():
    """אותה אפליקציה, פעם אחת לכל קובץ בדיקות.

    קובץ ההדגמה גדל לאלפי שורות, וייבוא שלו לוקח כתשע שניות. קובץ
    שרק קורא מהקטלוג לא צריך לשלם את זה בכל בדיקה בנפרד."""
    yield from _build_app()


@pytest.fixture
def client(app):
    """לקוח שכבר בתוך האפליקציה: הזדהה, ומנווט בין המסכים.

    בקשותיו נושאות מפנה מהאתר עצמו, כמו כל ניווט פנימי - זה מה שמבדיל
    אותו ממי שרק עכשיו פותח את האפליקציה ומקבל את מסך הפתיחה. מאז
    שהאפליקציה סגורה למי שלא הזדהה, הוא גם מזדהה: בלי זה כל בדיקה
    הייתה בודקת את מסך ההזדהות במקום את המסך שהיא באה לבדוק.
    """
    test_client = app.test_client()
    navigate = test_client.open

    def from_inside(*args, **kwargs):
        headers = dict(kwargs.pop("headers", None) or {})
        headers.setdefault("Referer", "http://localhost/parts")
        return navigate(*args, headers=headers, **kwargs)

    test_client.open = from_inside
    test_client.post("/login", data={"phone": FIXTURE_PHONE})
    return test_client


@pytest.fixture
def visitor(app):
    """לקוח שנוחת על האפליקציה בפעם הראשונה, לפני מסך הפתיחה."""
    return app.test_client()


@pytest.fixture
def identified(app):
    """מכשיר שכבר הזדהה, ומעכשיו פותח את האפליקציה כמו כל יום.

    בלי המפנה של client - הוא *פותח* את האפליקציה ולא מנווט בתוכה,
    וזה בדיוק מה שבדיקות מסך הפתיחה בוחנות.
    """
    test_client = app.test_client()
    test_client.post("/login", data={"phone": FIXTURE_PHONE})
    test_client.get("/")  # שורף את אסימון הכניסה שההזדהות נתנה
    return test_client


@pytest.fixture
def org_id(app):
    """מזהה ארגון ברירת המחדל של ה-fixture."""
    with app.app_context():
        return Organization.query.filter_by(slug="fixture-org").first().id


@pytest.fixture
def auth_client(app, client):
    """לקוח מזוהה כמנהל בארגון ברירת המחדל.

    נשאר כשם נפרד גם אחרי ש-client עצמו מזדהה, כי הוא מה שבדיקה
    אומרת כשחשוב לה *מי* מבצע את הפעולה ולא רק שהיא בוצעה.
    """
    return client
