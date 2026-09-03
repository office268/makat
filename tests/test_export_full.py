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
        Part.query.filter_by(part_number="ROUND-2").delete()
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
