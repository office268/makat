"""הורדת הקטלוג המלא, להבדיל מייצוא מה שרואים על המסך.

הייצוא הקיים נושא את המסננים של המסך, ו-``active_only`` הוא ברירת
המחדל שלו. כלומר "הורדתי הכל" היה שקר שקט: מק"ט שכובה לא היה בקובץ,
וזה בדיוק מה שגיבוי חייב לכלול.
"""
import csv
import io

from app.models import Part, db


def _rows(response):
    text = response.data.decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def _part(number, name, **extra):
    return Part(part_number=number, name_he=name, **extra)


def test_the_full_export_includes_a_part_that_was_switched_off(app, auth_client):
    with app.app_context():
        db.session.add(_part("ON-1", "פעיל", is_active=True))
        db.session.add(_part("OFF-1", "כבוי", is_active=False))
        db.session.commit()

    numbers = {r["part_number"] for r in _rows(auth_client.get("/export.csv?full=1"))}
    assert {"ON-1", "OFF-1"} <= numbers


def test_the_screen_export_still_leaves_it_out(app, auth_client):
    """שני הייצואים אינם זהים, וזו הסיבה ששניהם קיימים."""
    with app.app_context():
        db.session.add(_part("ON-2", "פעיל", is_active=True))
        db.session.add(_part("OFF-2", "כבוי", is_active=False))
        db.session.commit()

    numbers = {r["part_number"] for r in _rows(auth_client.get("/export.csv"))}
    assert "ON-2" in numbers
    assert "OFF-2" not in numbers


def test_the_full_export_ignores_the_filters_on_the_url(app, auth_client):
    """מי שמסנן ואז לוחץ "כל הקטלוג" ביקש את הכל, לא את הכל *שמתאים*."""
    with app.app_context():
        db.session.add(_part("AAA-1", "ראשון"))
        db.session.add(_part("BBB-1", "שני"))
        db.session.commit()

    numbers = {r["part_number"]
               for r in _rows(auth_client.get("/export.csv?full=1&q=AAA"))}
    assert {"AAA-1", "BBB-1"} <= numbers


def test_the_file_name_says_which_export_it_is(app, auth_client):
    full = auth_client.get("/export.csv?full=1")
    screen = auth_client.get("/export.csv")
    assert "makat_catalog_full.csv" in full.headers["Content-Disposition"]
    assert "makat_export.csv" in screen.headers["Content-Disposition"]


def test_the_columns_are_the_ones_the_import_accepts(app, auth_client):
    """הקובץ שיוצא צריך להיכנס בחזרה, אחרת הוא לא גיבוי אלא דוח."""
    from app import services

    with app.app_context():
        db.session.add(_part("ROUND-1", "הלוך ושוב"))
        db.session.commit()
    response = auth_client.get("/export.csv?full=1")
    header = response.data.decode("utf-8-sig").splitlines()[0]
    assert header.split(",") == list(services.CSV_COLUMNS)


def test_a_downloaded_catalog_can_be_imported_back(app, auth_client):
    """המבחן האמיתי: להוריד, למחוק, ולהחזיר."""
    from app import services

    with app.app_context():
        db.session.add(_part("ROUND-2", "הלוך ושוב", part_type="oil_filter"))
        db.session.commit()
    text = auth_client.get("/export.csv?full=1").data.decode("utf-8-sig")

    with app.app_context():
        db.session.delete(Part.query.filter_by(part_number="ROUND-2").first())
        db.session.commit()
        assert Part.query.filter_by(part_number="ROUND-2").first() is None
        services.import_csv(io.StringIO(text))
        back = Part.query.filter_by(part_number="ROUND-2").first()
        assert back is not None
        assert back.part_type == "oil_filter"


def test_the_full_export_is_written_to_the_activity_log(app, auth_client):
    from app.activity import ActivityLog

    auth_client.get("/export.csv?full=1")
    with app.app_context():
        note = ActivityLog.query.order_by(ActivityLog.id.desc()).first()
        assert "הקטלוג המלא" in note.summary


# --------------------------------------------------------------------------
# הורדה וטעינה הן אותו מבנה, ובאמת
# --------------------------------------------------------------------------

def test_every_field_the_import_reads_is_also_written_by_the_export():
    """השומר שהיה חסר.

    ``diagram_url`` היה בשדות שהייבוא *קורא* ולא באלה שהייצוא *כותב*,
    ולכן מסלול הלוך-ושוב מחק את קישור התרשים מכל מק"ט. שתי הרשימות
    חיות בשני מקומות, והבדיקה הזו היא מה שמחזיק אותן צמודות.
    """
    from app import services

    missing = [f for f in services.CATALOG_FIELDS if f not in services.CSV_COLUMNS]
    assert missing == [], f"נקרא בייבוא ואינו מיוצא: {missing}"


def test_no_catalog_field_of_a_part_is_left_out_of_the_file():
    """שדה במודל שאינו בקובץ הוא שדה שנעלם בגיבוי."""
    from app import services

    internal = {"id", "manufacturer_id", "category_id", "created_at", "updated_at"}
    missing = [c.name for c in Part.__table__.columns
               if c.name not in internal and c.name not in services.CSV_COLUMNS]
    assert missing == [], f'שדות Part שאינם בקובץ: {missing}'


def test_a_round_trip_keeps_every_single_field(app, auth_client):
    """המבחן האמיתי, ולא השוואת רשימות: למלא הכל, להוריד, למחוק, להחזיר."""
    from app import services

    filled = dict(
        part_number="ROUND-3", name_he="שם עברי", name_en="English name",
        description="תיאור", barcode="1234567890123", weight_kg=2.5,
        dimensions="10x20x30", warranty_months=24, side="קדמי",
        part_type="brake_pads_front", image_url="https://example.invalid/a.jpg",
        diagram_url="https://example.invalid/diagram.png", notes="הערה",
        is_active=True,
    )
    with app.app_context():
        db.session.add(Part(**filled))
        db.session.commit()

    text = auth_client.get("/export.csv?full=1").data.decode("utf-8-sig")

    with app.app_context():
        db.session.delete(Part.query.filter_by(part_number="ROUND-3").first())
        db.session.commit()
        services.import_csv(io.StringIO(text))
        back = Part.query.filter_by(part_number="ROUND-3").first()
        assert back is not None
        for field, value in filled.items():
            assert getattr(back, field) == value, field


def test_the_diagram_link_survives_the_round_trip(app, auth_client):
    """זה הקישור שנמחק בשקט עד עכשיו, ולכן הוא מקבל בדיקה משלו."""
    from app import services

    with app.app_context():
        db.session.add(Part(part_number="DIAG-1", name_he="עם תרשים",
                            diagram_url="https://example.invalid/4705.png"))
        db.session.commit()
    text = auth_client.get("/export.csv?full=1").data.decode("utf-8-sig")
    assert "https://example.invalid/4705.png" in text

    with app.app_context():
        db.session.delete(Part.query.filter_by(part_number="DIAG-1").first())
        db.session.commit()
        services.import_csv(io.StringIO(text))
        back = Part.query.filter_by(part_number="DIAG-1").first()
        assert back.diagram_url == "https://example.invalid/4705.png"


def test_cross_refs_and_fitments_survive_the_round_trip(app, auth_client):
    """שתי העמודות המורכבות: אם הפורמט אינו סימטרי, הן נעלמות בשקט."""
    from app import services
    from app.models import CrossReference, Fitment

    with app.app_context():
        part = Part(part_number="ROUND-4", name_he="עם קשרים")
        part.cross_refs.append(
            CrossReference(ref_type="OEM", ref_number="04152-YZZA1", ref_brand="Toyota"))
        part.fitments.append(
            Fitment(make="טויוטה", model="COROLLA", year_from=2013, year_to=2018,
                    engine_code="1ZR-FE"))
        db.session.add(part)
        db.session.commit()

    text = auth_client.get("/export.csv?full=1").data.decode("utf-8-sig")
    with app.app_context():
        db.session.delete(Part.query.filter_by(part_number="ROUND-4").first())
        db.session.commit()
        services.import_csv(io.StringIO(text))
        back = Part.query.filter_by(part_number="ROUND-4").first()
        assert [(r.ref_type, r.ref_number, r.ref_brand) for r in back.cross_refs] == \
            [("OEM", "04152-YZZA1", "Toyota")]
        fit = back.fitments[0]
        assert (fit.make, fit.model, fit.year_from, fit.year_to, fit.engine_code) == \
            ("טויוטה", "COROLLA", 2013, 2018, "1ZR-FE")
