"""מבט מכירה: הטבלה שעונה על מה ששואלים מעבר לדלפק.

השורה היא מק"ט, אבל המלאי והמחיר שבה הם של הקבוצה - המק"ט וכל
תחליפיו - כי מוכר שנשאל "יש לך?" אינו מוגבל למק"ט אחד.
"""
import pytest

from app import part_columns, services
from app.auth_models import Organization
from app.models import CrossReference, Fitment, OrgPart, Part, db


def body_of(response):
    html = response.get_data(as_text=True)
    return html.split("<tbody>")[1].split("</tbody>")[0]


@pytest.fixture
def counter(app):
    """קבוצה אחת: מקורי של טויוטה ושני תחליפיים, לאותו מספר מקורי.

    המקורי יקר, התחליפיים זולים, ולכל אחד יש מלאי משלו - כך שכל אחד
    משלושת המספרים שהטבלה מציגה (מקורי, טווח, סכום) שונה מהאחרים
    וטעות באחד מהם לא יכולה להתחבא מאחורי השני.
    """
    with app.app_context():
        organization = Organization.query.filter_by(slug="fixture-org").first()
        rows = [
            ("TOY-1", "TOYOTA", 400.0, 2),
            ("BOSCH-1", "BOSCH", 150.0, 5),
            ("FEBI-1", "FEBI BILSTEIN", 90.0, 3),
        ]
        for number, maker, price, qty in rows:
            part = Part(
                part_number=number, name_he="רפידות מרוץ", side="קדמי ימין",
                manufacturer=services.get_or_create_manufacturer(maker),
            )
            part.cross_refs = [
                CrossReference(ref_number="99465-02220", ref_type="OEM",
                               ref_brand="Toyota")
            ]
            part.fitments = [
                Fitment(make="טויוטה", model="COROLLA", engine_volume="1.6",
                        fuel="בנזין", year_from=2015, year_to=2020)
            ]
            db.session.add(part)
            db.session.flush()
            db.session.add(OrgPart(organization=organization, part=part, price=price,
                                   vat_included=True, stock_qty=qty))
        db.session.commit()
        yield organization


def sales_row(client, number):
    return body_of(client.get(f"/parts/sales?f_part_name=רפידות מרוץ&f_oe_all={number}"))


# ---------- הדף עצמו ----------

def test_the_page_opens_with_all_eight_columns(client, counter):
    response = client.get("/parts/sales")
    assert response.status_code == 200
    header = response.get_data(as_text=True).split("<thead")[1].split("</thead>")[0]
    for label in ["רכב", "חלק", "OE", "תמונה", "ביקוש", "תחליפים", "מלאי", "מחיר"]:
        assert label in header, label


def test_the_catalog_table_is_untouched(client, counter):
    """הטבלה החדשה נוספת, לא מחליפה."""
    header = client.get("/parts").get_data(as_text=True).split("<thead")[1]
    assert 'מק"ט' in header
    assert "ביקוש" not in header.split("</thead>")[0]


def test_a_visitor_without_an_organization_still_gets_the_page(visitor):
    """בלי ארגון אין מלאי ואין מחיר - אבל גם אין 500."""
    assert visitor.get("/parts/sales").status_code == 302


# ---------- התאים המאוחדים ----------

def test_the_vehicle_cell_joins_what_exists_and_skips_what_does_not(client, counter):
    """נתון חסר נשמט, ולא מוצג כשדה ריק.

    להתאמה כאן יש יצרן, דגם, נפח, דלק ושנים - אבל אין תיבת הילוכים,
    כי אין בכלל שדה כזה במערכת. התא מציג את מה שיש ברצף אחד, בלי
    מקפים באמצע שמסמנים חורים.
    """
    row = sales_row(client, "99465")
    assert "טויוטה COROLLA 1.6 בנזין 2015–2020" in row


def test_the_part_cell_joins_the_name_and_where_it_sits(client, counter):
    assert "רפידות מרוץ · קדמי ימין" in sales_row(client, "99465")


def test_every_original_number_is_shown_not_just_the_first(app, client, counter):
    """בטבלת הקטלוג מוצג הראשון ו"+N". כאן מזמינים לפי המספר, וצריך את כולם."""
    with app.app_context():
        part = Part.query.filter_by(part_number="TOY-1").first()
        part.cross_refs.append(
            CrossReference(ref_number="99465-42160", ref_type="OEM", ref_brand="Toyota")
        )
        db.session.commit()
    row = sales_row(client, "99465-42160")
    assert "99465-02220" in row and "99465-42160" in row


def test_a_part_without_a_picture_says_so_instead_of_breaking(client, counter):
    """אין תמונה לאף מק"ט בקטלוג היום. התא חייב להתמודד עם זה בשקט."""
    row = sales_row(client, "99465")
    assert "<img" not in row
    assert "—" in row


# ---------- המספרים של הקבוצה ----------

def test_the_stock_is_the_whole_group_not_one_part(app, counter):
    """2 + 5 + 3: מה שאפשר להציע ללקוח, ולא מה שיש ממק"ט אחד."""
    with app.app_context():
        parts = Part.query.filter(Part.part_number.in_(["TOY-1", "BOSCH-1", "FEBI-1"])).all()
        columns = part_columns.resolve(part_columns.SALES_KEYS)
        counts = services.column_counts(parts, columns, counter.id)
        for part in parts:
            assert counts[part.id]["group_stock"] == 10, part.part_number


def test_the_price_range_spans_the_group(app, counter):
    with app.app_context():
        part = Part.query.filter_by(part_number="TOY-1").first()
        row = services.column_counts(
            [part], part_columns.resolve(part_columns.SALES_KEYS), counter.id
        )[part.id]
        assert row["cheapest"] == pytest.approx(90.0)
        assert row["dearest"] == pytest.approx(400.0)


def test_only_the_manufacturer_of_the_car_counts_as_original(app, counter):
    """אין שדה שאומר "מקורי". הסימן היחיד: יצרן החלק == המותג שעל ה-OE.

    TOYOTA שמוכרת חלק שהמקור שלו רשום על שם Toyota היא המקורי; BOSCH
    לאותו מספר היא תחליפי. כשאין סימן, התא נשאר ריק - עדיף מניחוש.
    """
    with app.app_context():
        columns = part_columns.resolve(part_columns.SALES_KEYS)
        for number, expected in [("TOY-1", 400.0), ("BOSCH-1", None), ("FEBI-1", None)]:
            part = Part.query.filter_by(part_number=number).first()
            price = services.column_counts([part], columns, counter.id)[part.id]["original_price"]
            if expected is None:
                assert price is None, number
            else:
                assert price == pytest.approx(expected), number


def test_a_lone_part_is_its_own_group(app, counter):
    """מק"ט בלי תחליפים - הקבוצה היא הוא עצמו, לא אפס."""
    with app.app_context():
        lone = Part(part_number="SOLO-9", name_he="בודד")
        db.session.add(lone)
        db.session.flush()
        db.session.add(OrgPart(organization_id=counter.id, part=lone, price=77.0,
                               vat_included=True, stock_qty=4))
        db.session.commit()
        part = Part.query.filter_by(part_number="SOLO-9").first()
        row = services.column_counts(
            [part], part_columns.resolve(part_columns.SALES_KEYS), counter.id
        )[part.id]
        assert row["group_stock"] == 4
        assert row["cheapest"] == pytest.approx(77.0)
        assert row["dearest"] == pytest.approx(77.0)


# ---------- מיון וסינון ----------

@pytest.mark.parametrize("sort", [
    "vehicle:asc", "vehicle:desc",
    "part_name:asc", "part_name:desc",
    "image:asc", "image:desc",
    "demand:desc",
    "group_stock:asc", "group_stock:desc",
    "group_price:asc", "group_price:desc",
])
def test_every_sales_column_sorts(client, counter, sort):
    response = client.get(f"/parts/sales?sort={sort}")
    assert response.status_code == 200
    assert "רפידות מרוץ" in body_of(response)


def test_sorting_by_price_uses_the_cheapest_in_the_group(app, client, counter):
    """התא מציג טווח; המיון הוא לפי הקצה הזול, שהוא אחד משני המספרים שבתא."""
    with app.app_context():
        lone = Part(part_number="SOLO-1", name_he="בודד")
        db.session.add(lone)
        db.session.flush()
        organization = Organization.query.filter_by(slug="fixture-org").first()
        db.session.add(OrgPart(organization=organization, part=lone, price=20.0,
                               vat_included=True, stock_qty=1))
        db.session.commit()
    row = body_of(client.get("/parts/sales?sort=group_price:asc"))
    assert row.index("בודד") < row.index("רפידות מרוץ")


@pytest.mark.parametrize("param,value,found", [
    ("f_vehicle", "קורולה", False),
    ("f_vehicle", "COROLLA", True),
    ("f_vehicle", "בנזין", True),
    ("f_part_name", "קדמי ימין", True),
    ("f_oe_all", "99465", True),
    ("f_image", "0", True),
    ("f_image", "1", False),
    ("f_group_stock", ">5", True),
    ("f_group_stock", ">50", False),
])
def test_the_sales_columns_filter(client, counter, param, value, found):
    row = body_of(client.get(f"/parts/sales?{param}={value}"))
    assert ("רפידות מרוץ" in row) is found


def test_a_half_typed_number_filter_does_not_empty_the_table(client, counter):
    assert "רפידות מרוץ" in body_of(client.get("/parts/sales?f_group_stock=לא מספר"))
