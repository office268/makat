"""קובץ ההדגמה: הוא קיים כדי שהזרימה תעבוד, ולכן זה מה שנבדק."""
import pathlib

from app import services
from app.models import Part, db

CSV = pathlib.Path(__file__).resolve().parent.parent / "data" / "demo_parts.csv"


def _load(app):
    with app.app_context():
        Part.query.delete()
        db.session.commit()
        with CSV.open(encoding="utf-8-sig") as fh:
            return services.import_csv(fh)


def test_file_imports_without_errors(app):
    created, updated, errors = _load(app)
    assert errors == []
    assert created >= 60


def test_plate_to_part_number_actually_resolves(app):
    """הזרימה שבשבילה הקובץ קיים: רכב אמיתי -> מק"ט אמיתי."""
    _load(app)
    with app.app_context():
        peugeot = {"make": "פיג'ו צרפת", "model": "5008", "year": 2020}
        matches = services.parts_for_vehicle(peugeot, "oil_filter")
        assert matches, "5008 מ-2020 חייב להחזיר מסנן שמן"
        assert {p.part_number for p in matches} >= {"32223", "H90W23"}

        corolla = {"make": "טויוטה יפן", "model": "COROLLA", "year": 2016}
        assert services.parts_for_vehicle(corolla, "oil_filter")


def test_year_range_is_real_not_decorative(app):
    """5008 II יצא ב-2016; רכב מ-2005 לא אמור להחזיר את החלפים שלו."""
    _load(app)
    with app.app_context():
        old = {"make": "פיג'ו צרפת", "model": "5008", "year": 2005}
        assert services.parts_for_vehicle(old, "oil_filter") == []


def test_every_row_is_marked_as_demo_data(app):
    """מקור הנתונים לא רשמי, ולכן כל שורה נושאת את זה בגלוי."""
    _load(app)
    with app.app_context():
        parts = Part.query.all()
        assert parts
        assert all("נתוני הדגמה" in (p.notes or "") for p in parts)


def test_shared_part_number_keeps_every_fitment(app):
    """מק"ט אחד מתאים לכמה דגמים, ולכן שורה אחת נושאת את כל ההתאמות.

    שתי שורות עם אותו מק"ט היו דורסות זו את זו - הייבוא מחליף את
    אוסף ההתאמות, ולא מוסיף לו.
    """
    _load(app)
    with app.app_context():
        part = Part.query.filter_by(part_number="W 67/1").one()
        assert {f.model for f in part.fitments} == {"PICANTO", "QASHQAI", "CLIO"}


def test_catalog_covers_the_common_israeli_models(app):
    _load(app)
    with app.app_context():
        expected = [
            ("פיג'ו צרפת", "5008", 2020), ("טויוטה יפן", "COROLLA", 2016),
            ("טויוטה יפן", "YARIS", 2018), ("יונדאי קוריאה", "i20", 2021),
            ("קיה קוריאה", "PICANTO", 2019), ("קיה קוריאה", "SPORTAGE", 2020),
            ("מאזדה יפן", "3", 2017), ("סקודה צ'כיה", "OCTAVIA", 2019),
            ("ניסאן יפן", "QASHQAI", 2018), ("רנו צרפת", "CLIO", 2020),
            ("יונדאי קוריאה", "TUCSON", 2022),
        ]
        for make, model, year in expected:
            vehicle = {"make": make, "model": model, "year": year}
            assert services.catalog_coverage(vehicle), f"{make} {model} בלי חלפים"


def test_catalog_spans_several_part_types(app):
    """קטלוג של מסנני שמן בלבד לא מדגים כלום - צריך רוחב."""
    _load(app)
    with app.app_context():
        types = {p.part_type for p in Part.query.all()}
        assert {"oil_filter", "air_filter", "cabin_filter",
                "wiper_blade", "fuel_filter"} <= types


def test_corolla_answers_more_than_one_question(app):
    """הדגם המוביל בהדגמה - כמה סוגי חלקים על אותו רכב."""
    _load(app)
    with app.app_context():
        corolla = {"make": "טויוטה יפן", "model": "COROLLA", "year": 2016}
        coverage = services.catalog_coverage(corolla)
        assert len(coverage) >= 5, coverage
        assert sum(coverage.values()) >= 15


def test_oe_cross_references_came_through(app):
    _load(app)
    with app.app_context():
        part = Part.query.filter_by(part_number="27149").one()
        assert [r.ref_number for r in part.cross_refs] == ["90915-YZZJ1"]
