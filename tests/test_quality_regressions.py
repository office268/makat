"""בדיקות נסיגה לתקלות שנמצאו בבקרת האיכות.

כל בדיקה כאן נכשלה לפני התיקון ועוברת אחריו. הן מקובצות בקובץ אחד
לפי הכשל שהן שומרות עליו, ולא לפי המודול שבו הוא ישב.
"""
import io

import pytest

from app import live_lookup, parts_discovery, services
from app.auth import safe_target
from app.auth_models import Organization, User
from app.models import Fitment, OrgPart, Part, db


# --------------------------------------------------------------------------
# ייבוא CSV: שורה פגומה מפילה את עצמה בלבד
# --------------------------------------------------------------------------

def test_bad_row_does_not_discard_the_rows_before_it(app, org_id):
    """שורה שנפלה גררה rollback על כל מה שהצטבר לפניה.

    המונים המשיכו לספור, המסך בישר "יובאו ארבעה", ובבסיס הנתונים
    נשאר אחד - בלי שאיש ידע שהשאר נעלמו.
    """
    with app.app_context():
        real = services.part_from_row

        def flaky(row, part=None, **kwargs):
            if row.get("part_number") == "BAD":
                raise ValueError("שדה פגום")
            return real(row, part, **kwargs)

        services.part_from_row = flaky
        try:
            created, updated, errors = services.import_csv(
                io.StringIO(
                    "part_number,name_he\n"
                    "Q-1,אחד\nQ-2,שניים\nQ-3,שלושה\nBAD,נופל\nQ-5,חמישה\n"
                ),
                organization_id=org_id,
            )
        finally:
            services.part_from_row = real

        saved = {p.part_number for p in Part.query.all()}
        assert {"Q-1", "Q-2", "Q-3", "Q-5"} <= saved
        assert "BAD" not in saved
        assert len(errors) == 1
        # המספר שהמסך מציג הוא המספר שנשמר באמת
        assert created == 4


# --------------------------------------------------------------------------
# ייבוא חלקי: עמודה שלא נמסרה אינה מתאפסת
# --------------------------------------------------------------------------

def test_price_only_import_keeps_cost_stock_and_location(app, org_id):
    """מחירון ספק עם עמודת מחיר בלבד עדכן מחיר ומחק את כל השאר."""
    with app.app_context():
        services.import_csv(
            io.StringIO(
                "part_number,name_he,price,cost,stock_qty,location\n"
                "PARTIAL-1,חלק,100,60,7,מדף א\n"
            ),
            organization_id=org_id,
        )
        services.import_csv(
            io.StringIO("part_number,name_he,price\nPARTIAL-1,חלק,120\n"),
            organization_id=org_id,
        )
        db.session.expire_all()
        part = Part.query.filter_by(part_number="PARTIAL-1").one()
        link = OrgPart.query.filter_by(
            organization_id=org_id, part_id=part.id
        ).one()
        assert link.price == 120.0
        assert link.cost == 60.0
        assert link.stock_qty == 7
        assert link.location == "מדף א"


def test_the_form_still_saves_every_field_it_shows(client, app, org_id):
    """הטופס שולח את כל שדותיו, ולכן השמירה ממנו לא השתנתה."""
    with app.app_context():
        part = Part.query.filter_by(part_number="TEST-001").one()
        part_id = part.id

    client.post(
        f"/parts/{part_id}/edit",
        data={
            "part_number": "TEST-001",
            "name_he": "רפידות",
            "price": "250", "cost": "150",
            "stock_qty": "9", "min_stock": "3", "location": "ב-07",
        },
    )
    with app.app_context():
        link = OrgPart.query.filter_by(
            organization_id=org_id, part_id=part_id
        ).one()
        assert (link.price, link.cost) == (250.0, 150.0)
        assert (link.stock_qty, link.min_stock, link.location) == (9, 3, "ב-07")


# --------------------------------------------------------------------------
# הפניה אחרי הזדהות
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "target",
    ["//evil.example", "http://evil.example", "/\\evil.example", "/\\/evil.example"],
)
def test_login_redirect_refuses_to_leave_the_site(app, target):
    """לוכסן הפוך: הדפדפן מתרגם "\\" ל-"/", ולכן /\\evil.example הוא
    //evil.example - הפניה החוצה שעברה את הבדיקה כנתיב פנימי."""
    with app.test_request_context("/"):
        assert safe_target(target) == "/"


def test_login_redirect_keeps_an_internal_path(app):
    with app.test_request_context("/"):
        assert safe_target("/parts?q=בלם") == "/parts?q=בלם"


# --------------------------------------------------------------------------
# שליפה חיה: עבודה של מישהו אחר
# --------------------------------------------------------------------------

def test_cancel_refuses_a_job_that_belongs_to_someone_else(app, org_id):
    """מזהה העבודה הוא מספר רץ; בלי הבדיקה די היה לנחש אותו."""
    with app.app_context():
        other_org = Organization(name="מוסך אחר", slug="other-org")
        db.session.add(other_org)
        db.session.flush()
        owner = User.query.filter_by(phone="0500000001").one()
        intruder = User(
            phone="0500000099", email="other@t.test", role="owner",
            organization=other_org,
        )
        db.session.add(intruder)
        job = live_lookup.LookupJob(
            plate="1234567", vin_key="vin:TEST", part_type="brake_pads_front",
            vehicle="{}", stages='["mock"]',
            results='{"results": [], "unverified": []}',
            organization_id=org_id, started_by_id=owner.id,
        )
        db.session.add(job)
        db.session.commit()
        job_id, intruder_id = job.id, intruder.id

    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(intruder_id)
        session["_fresh"] = True

    assert client.post("/lookup/cancel", data={"job": job_id}).status_code == 403
    assert client.post("/lookup/step", data={"job": job_id}).status_code == 403
    with app.app_context():
        assert db.session.get(live_lookup.LookupJob, job_id).is_running


# --------------------------------------------------------------------------
# הקטלוג מתרחב עם כל שנת דגם שנשלפת
# --------------------------------------------------------------------------

def test_a_second_model_year_widens_the_fitment(app):
    """שליפה חיה שומרת שנה מדויקת. קודם השנייה נבלעה בשקט: הבדיקה
    ל"התאמה קיימת" השוותה יצרן ודגם בלבד, וכל שנה נוספת שילמה על
    שליפה שממנה לא נשמר דבר."""
    row = {
        "part_number": "WIDEN-1", "manufacturer": "MAHLE",
        "part_type": "oil_filter", "make": "טויוטה", "model": "COROLLA",
        "year": 2015, "engine_code": "2ZR-FAE",
    }
    with app.app_context():
        parts_discovery.save([row])
        parts_discovery.save([dict(row, year=2016)])

        fitments = Fitment.query.filter(Fitment.model == "COROLLA").all()
        mine = [f for f in fitments if f.part.part_number == "WIDEN-1"]
        assert len(mine) == 1, "שנה נוספת אינה יוצרת התאמה כפולה"
        assert (mine[0].year_from, mine[0].year_to) == (2015, 2016)

        for year in (2015, 2016):
            found = services.parts_for_vehicle(
                {"make": "טויוטה", "model": "COROLLA", "year": year}, "oil_filter"
            )
            assert "WIDEN-1" in {p.part_number for p in found}


def test_a_different_engine_gets_its_own_fitment(app):
    """מנוע אחר אינו אותה התאמה, ולכן הוא לא נבלע בתוכה."""
    row = {
        "part_number": "WIDEN-2", "manufacturer": "MAHLE",
        "part_type": "oil_filter", "make": "טויוטה", "model": "COROLLA",
        "year": 2015, "engine_code": "2ZR-FAE",
    }
    with app.app_context():
        parts_discovery.save([row])
        parts_discovery.save([dict(row, engine_code="1NZ-FE", year=2016)])
        part = Part.query.filter_by(part_number="WIDEN-2").one()
        assert {f.engine_code for f in part.fitments} == {"2ZR-FAE", "1NZ-FE"}


# --------------------------------------------------------------------------
# מספר השאילתות אינו גדל עם הקטלוג
# --------------------------------------------------------------------------

def _count_queries(app, call):
    """כמה שאילתות יצאו לבסיס הנתונים במהלך ``call``."""
    from sqlalchemy import event

    with app.app_context():
        engine = db.engine
    counter = []

    def record(*args, **kwargs):
        counter.append(1)

    event.listen(engine, "before_cursor_execute", record)
    try:
        call()
    finally:
        event.remove(engine, "before_cursor_execute", record)
    return len(counter)


def test_export_does_not_query_once_per_row(client, app):
    """הייצוא טען יצרן, קטגוריה, מקבילים, התאמות ושכבת ארגון לכל שורה
    בנפרד - חמש שאילתות למק"ט. הבדיקה מוודאת שהמספר קבוע ולא גדל."""
    with app.app_context():
        for index in range(30):
            db.session.add(
                Part(part_number=f"EXPORT-{index}", name_he=f"חלק {index}")
            )
        db.session.commit()

    few = _count_queries(app, lambda: client.get("/export.csv"))

    with app.app_context():
        for index in range(30, 120):
            db.session.add(
                Part(part_number=f"EXPORT-{index}", name_he=f"חלק {index}")
            )
        db.session.commit()

    many = _count_queries(app, lambda: client.get("/export.csv"))
    assert many == few, f"הייצוא גדל מ-{few} ל-{many} שאילתות על פי שלוש שורות"


def test_category_and_manufacturer_screens_count_in_one_query(client, app):
    """‎'| length' על אוסף הקשר שלף את כל המק"טים רק כדי למנות אותם."""
    with app.app_context():
        from app.services import get_or_create_category, get_or_create_manufacturer

        for index in range(40):
            get_or_create_category(f"קטגוריה {index}")
            get_or_create_manufacturer(f"יצרן {index}")
        db.session.commit()

    assert _count_queries(app, lambda: client.get("/categories")) < 15
    assert _count_queries(app, lambda: client.get("/manufacturers")) < 15


# --------------------------------------------------------------------------
# שדה שלא נמסר אינו נמחק - גם בשדות הקטלוג, לא רק במסחריים
# --------------------------------------------------------------------------

CATALOG_ROW = (
    "part_number,name_he,name_en,description,barcode,part_type,weight_kg,"
    "side,warranty_months,image_url,notes\n"
    "KEEP-1,רפידות,Brake Pads,תיאור,7290001,brake_pads_front,1.4,"
    "קדמי,24,https://x.example/a.jpg,הערה\n"
)


def test_a_price_list_does_not_wipe_the_catalog_fields(app, org_id):
    """מחירון ספק נושא שלוש עמודות. קודם הוא מחק תשעה שדות מכל מק"ט
    בקובץ - ובראשם part_type, המפתח שמקשר מק"ט לזרימת הזיהוי."""
    with app.app_context():
        services.import_csv(io.StringIO(CATALOG_ROW), organization_id=org_id)
        services.import_csv(
            io.StringIO("part_number,name_he,price\nKEEP-1,רפידות,120\n"),
            organization_id=org_id,
        )
        db.session.expire_all()
        part = Part.query.filter_by(part_number="KEEP-1").one()
        assert part.part_type == "brake_pads_front"
        assert part.name_en == "Brake Pads"
        assert part.description == "תיאור"
        assert part.barcode == "7290001"
        assert part.weight_kg == 1.4
        assert part.side == "קדמי"
        assert part.warranty_months == 24
        assert part.image_url == "https://x.example/a.jpg"
        assert part.notes == "הערה"


def test_a_part_the_pipeline_added_stays_in_the_review_queue(client, app):
    """הטופס אינו שולח image_url ו-notes, ולכן כל שמירה מחקה אותם.

    ‏notes נושא את סימון המקור, וכך מק"ט שהגילוי האוטומטי הכניס יצא
    מ-/admin/discovery/review - התור שקיים בדיוק כדי לסקור אותו -
    ברגע שמנהל תיקן בו טעות כתיב.
    """
    from app import parts_discovery

    with app.app_context():
        parts_discovery.save([{
            "part_number": "REVIEW-1", "manufacturer": "MAHLE",
            "part_type": "oil_filter", "make": "טויוטה", "model": "COROLLA",
            "year": 2015, "image_url": "https://cdn.example/oc90.jpg",
        }])
        part = Part.query.filter_by(part_number="REVIEW-1").one()
        assert parts_discovery.SOURCE_MARK in part.notes
        part_id = part.id

    client.post(f"/parts/{part_id}/edit", data={
        "part_number": "REVIEW-1", "name_he": "מסנן שמן קורולה",
        "name_en": "", "description": "", "barcode": "",
        "part_type": "oil_filter", "manufacturer": "MAHLE",
    })

    with app.app_context():
        part = db.session.get(Part, part_id)
        assert part.name_he == "מסנן שמן קורולה", "העריכה עצמה כן נשמרה"
        assert part.image_url == "https://cdn.example/oc90.jpg"
        assert parts_discovery.SOURCE_MARK in (part.notes or "")
        assert part in parts_discovery.discovered_parts()


def test_clearing_a_field_from_the_form_still_clears_it(client, app, org_id):
    """שדה ריק בטופס *נשלח* כמחרוזת ריקה - כלומר נמסר, ונכתב כ-None.

    זו הבקרה על התיקון: "לא נמסר" ו"נמסר ריק" הם שני דברים שונים.
    """
    with app.app_context():
        services.import_csv(io.StringIO(CATALOG_ROW), organization_id=org_id)
        part_id = Part.query.filter_by(part_number="KEEP-1").one().id

    client.post(f"/parts/{part_id}/edit", data={
        "part_number": "KEEP-1", "name_he": "רפידות",
        "name_en": "", "description": "", "barcode": "", "side": "",
        "part_type": "brake_pads_front",
    })
    with app.app_context():
        part = db.session.get(Part, part_id)
        assert part.name_en is None
        assert part.description is None
        assert part.barcode is None
        assert part.side is None
        assert part.part_type == "brake_pads_front", "מה שנשלח עם ערך נשמר"


def test_api_patch_still_replaces_every_field_it_was_given(client, app, org_id):
    """‏PATCH בונה מיזוג מהמצב הנוכחי, ולכן כל השדות נמסרים בו כרגיל."""
    with app.app_context():
        services.import_csv(io.StringIO(CATALOG_ROW), organization_id=org_id)
        part_id = Part.query.filter_by(part_number="KEEP-1").one().id

    response = client.patch(
        f"/api/parts/{part_id}", json={"name_en": "Front Pads", "barcode": ""}
    )
    assert response.status_code == 200
    with app.app_context():
        part = db.session.get(Part, part_id)
        assert part.name_en == "Front Pads"
        assert part.barcode is None
        assert part.part_type == "brake_pads_front", "שדה שלא נגעו בו נשמר"
        assert part.image_url == "https://x.example/a.jpg"
