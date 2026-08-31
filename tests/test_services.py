"""חיפוש, ייבוא וסטטיסטיקות."""
import io

from app import services

from app.auth_models import Organization
from app.models import OrgPart, Part
from app.services import (
    export_csv,
    find_by_number,
    import_csv,
    parts_for_vehicle,
    search_parts,
    stats,
)


def test_find_by_number_matches_part_and_cross_ref(app):
    assert find_by_number("TEST-001").part_number == "TEST-001"
    assert find_by_number("04465-02220").part_number == "TEST-001"
    assert find_by_number("nope") is None


def test_search_by_free_text_hits_cross_refs(app):
    assert search_parts(q="04465").count() == 1
    assert search_parts(q="רפידות").count() == 1


def test_search_filters_by_vehicle(app):
    assert search_parts(make="טויוטה", model="COROLLA", year=2015).count() == 1
    assert search_parts(make="טויוטה", year=2020).count() == 0   # מחוץ לטווח השנים
    assert search_parts(make="מאזדה").count() == 0


def test_parts_for_vehicle_requires_matching_type(app):
    vehicle = {"make": "טויוטה יפן", "model": "COROLLA", "year": 2016}
    assert len(parts_for_vehicle(vehicle, "brake_pads_front")) == 1
    assert len(parts_for_vehicle(vehicle, "oil_filter")) == 0


def test_price_with_vat_and_margin(app, org_id):
    """המחיר נמצא בשכבה הפרטית, לא בקטלוג המשותף."""
    link = OrgPart.query.filter_by(organization_id=org_id).first()
    assert link.price_with_vat == 236.0        # 200 * 1.18
    assert link.margin_percent == 30.0         # (200-140)/200
    assert not hasattr(Part.query.first(), "price")


def test_import_creates_then_updates(app, org_id):
    """הקטלוג נכתב משותף; המחיר נכתב לשכבה של הארגון המייבא."""
    csv_text = (
        "part_number,name_he,manufacturer,category,price,stock_qty,part_type,fitments\n"
        "IMP-1,מסנן שמן,Mahle,מנוע / סינון,45,10,oil_filter,מאזדה:MAZDA 3:2014:2019:PE-VPS\n"
    )
    created, updated, errors = import_csv(io.StringIO(csv_text), organization_id=org_id)
    assert (created, updated, errors) == (1, 0, [])
    part = Part.query.filter_by(part_number="IMP-1").first()
    assert part.part_type == "oil_filter"
    assert part.fitments[0].make == "מאזדה"
    assert part.for_org(org_id).price == 45

    created, updated, errors = import_csv(
        io.StringIO(csv_text.replace(",45,", ",99,")), organization_id=org_id
    )
    assert (created, updated) == (0, 1)
    assert Part.query.filter_by(part_number="IMP-1").first().for_org(org_id).price == 99


def test_import_reports_missing_required_fields(app):
    _created, _updated, errors = import_csv(
        io.StringIO("part_number,name_he\n,חלק בלי מקט\nX-1,\n")
    )
    assert len(errors) == 2


def test_export_round_trips_through_import(app, org_id):
    text = export_csv(Part.query.all(), organization_id=org_id)
    assert "TEST-001" in text
    assert "04465-02220" in text          # מק"ט מקביל נשמר
    assert "טויוטה:COROLLA:2013:2018" in text


def test_stats_counts(app, org_id):
    result = stats(org_id)
    assert result["parts"] == 1
    assert result["in_stock"] == 1      # מלאי שייך לארגון
    assert result["cross_refs"] == 1

    anonymous = stats(None)
    assert anonymous["parts"] == 1      # הקטלוג משותף
    assert anonymous["in_stock"] == 0   # המלאי לא נחשף


def test_model_match_ignores_spaces_and_hyphens(app, org_id):
    """כתיב הדגם לא זהה בין המקורות, וזה לא אמור להסתיר מק"ט.

    מאגר משרד התחבורה כותב "RAV 4" או "RAV4", "C-HR" או "CHR", ורשימות
    החלפים כותבות את השני. התאמה שנשמרה בכתיב אחד חייבת להימצא גם
    כשהרכב חוזר מהמאגר בכתיב השני.
    """
    from app.models import Fitment, Part, db
    from app.services import parts_for_vehicle

    with app.app_context():
        part = Part(part_number="SPACE-1", name_he="מסנן שמן", part_type="oil_filter")
        part.fitments = [Fitment(make="טויוטה", model="RAV4"),
                         Fitment(make="טויוטה", model="C-HR")]
        db.session.add(part)
        db.session.commit()

        for model in ("RAV4", "RAV 4", "rav 4", "C-HR", "CHR", "c hr"):
            vehicle = {"make": "טויוטה יפן", "model": model, "year": 2020}
            found = parts_for_vehicle(vehicle, "oil_filter")
            assert [p.part_number for p in found] == ["SPACE-1"], model


def test_partial_model_search_still_works(app, org_id):
    """הכיווץ לא הופך את החיפוש למדויק - חיפוש חלקי ממשיך למצוא."""
    from app.models import Fitment, Part, db
    from app.services import parts_for_vehicle

    with app.app_context():
        part = Part(part_number="PART-1", name_he="מסנן", part_type="oil_filter")
        part.fitments = [Fitment(make="מאזדה", model="MAZDA 3")]
        db.session.add(part)
        db.session.commit()

        typed_by_hand = {"make": "מאזדה יפן", "model": "3", "year": 2018}
        assert parts_for_vehicle(typed_by_hand, "oil_filter")


# ---- זיהוי מנוע ----


def _engine_fixture(app):
    """הקטלוג האמיתי מדבר שני כתיבים: נפח ורמת מנוע, וקוד יצרן."""
    from app.models import Fitment, Part, db
    from app.vehicle_catalog import VehicleModel

    with app.app_context():
        for number, engine in (("ENG-VOL", "1.4 TSI"),      # כתיב נפח
                               ("ENG-CODE", "2ZR-FAE"),      # כתיב קוד יצרן
                               ("ENG-OTHER", "1.6 GDI"),     # מנוע אחר
                               ("ENG-NONE", None)):          # לא צוין מנוע
            part = Part(part_number=number, name_he=f"מסנן שמן {number}",
                        part_type="oil_filter")
            part.fitments = [Fitment(make="סקודה", model="OCTAVIA",
                                     engine_code=engine)]
            db.session.add(part)
        db.session.add(VehicleModel(make="סקודה", model="OCTAVIA",
                                    model_code="5E3", engine_volume=1395))
        db.session.commit()


VEHICLE = {"make": "סקודה צכיה", "model": "OCTAVIA", "model_code": "5E3",
           "engine_code": "2ZRFAE"}


def test_engine_terms_cover_both_notations(app):
    """המרשם מוסר קוד יצרן; הנפח מגיע מקטלוג הדגמים שלנו."""
    _engine_fixture(app)
    with app.app_context():
        assert services.vehicle_engine_terms(VEHICLE) == ["2ZRFAE", "1.4"]


def test_engine_terms_without_a_model_in_the_catalog(app):
    """בלי קטלוג דגמים אין נפח, ונשאר רק מה שהמרשם מסר."""
    with app.app_context():
        assert services.vehicle_engine_terms(
            {"make": "סקודה צכיה", "model": "OCTAVIA", "model_code": "?",
             "engine_code": "2ZRFAE"}
        ) == ["2ZRFAE"]
        assert services.vehicle_engine_terms({"make": "סקודה"}) == []


def test_both_notations_are_recognised_across_spelling(app):
    """"2ZR-FAE" בקטלוג ו-"2ZRFAE" במרשם הם אותו מנוע."""
    _engine_fixture(app)
    with app.app_context():
        parts = services.parts_for_vehicle(VEHICLE, "oil_filter")
        verified = services.engine_matched_parts(
            parts, services.vehicle_engine_terms(VEHICLE))
        numbers = {p.part_number for p in parts if p.id in verified}
        assert numbers == {"ENG-VOL", "ENG-CODE"}


def test_engine_is_marked_and_never_filters(app):
    """הקטלוג דליל מדי לסינון: מק"ט למנוע אחר עדיין מוצג, רק בלי סימון."""
    _engine_fixture(app)
    with app.app_context():
        parts = services.parts_for_vehicle(VEHICLE, "oil_filter")
        assert {p.part_number for p in parts} == {
            "ENG-VOL", "ENG-CODE", "ENG-OTHER", "ENG-NONE"}

        verified = services.engine_matched_parts(parts, [])
        assert verified == set()  # בלי מנוע ידוע אין מה לאמת


def test_explicit_engine_filter_keeps_fitments_without_an_engine(app):
    """סינון מפורש כן מצמצם - אבל "לא צוין מנוע" אינו "לא מתאים"."""
    _engine_fixture(app)
    with app.app_context():
        found = {p.part_number for p in services.search_parts(
            make="סקודה", model="OCTAVIA", engine="1.4").all()}
        assert found == {"ENG-VOL", "ENG-NONE"}


def test_verified_parts_come_first_on_the_identify_screen(client, app):
    """מסך הזיהוי מעלה את המאומתים לראש - זה מה שהופך את הסימון לשימושי.

    רכב הדוגמה 56789012 הוא סקודה אוקטביה עם קוד מנוע CZCA, ולכן הוא
    מזהה את ההתאמה שכתובה באותו קוד.
    """
    from app.models import Fitment, Part, db

    with app.app_context():
        for number, engine in (("ZZZ-9", "CZCA"), ("AAA-1", None)):
            part = Part(part_number=number, name_he=f"מסנן שמן {number}",
                        part_type="oil_filter")
            part.fitments = [Fitment(make="סקודה", model="OCTAVIA",
                                     engine_code=engine)]
            db.session.add(part)
        db.session.commit()

    html = client.post("/", data={"plate": "56789012", "query": "מסנן שמן"},
                       follow_redirects=True).get_data(as_text=True)

    assert "ZZZ-9" in html and "AAA-1" in html   # אף אחד לא הוסתר
    assert html.index("ZZZ-9") < html.index("AAA-1")  # והמאומת ראשון
    assert "מנוע תואם" in html
