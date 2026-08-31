"""עמודות טבלת המק"טים: סדר, מיון, סינון, והוספה והסרה."""
import pytest

from app import part_columns, services
from app.auth_models import Organization, User
from app.models import CrossReference, OrgPart, Part, TableLayout, db
from app.services import get_or_create_manufacturer

SUPERADMIN = "boss@t.test"
SUPERADMIN_PHONE = "0507770001"


@pytest.fixture
def catalog(app):
    """שלושה מק"טים עם מחיר, מלאי ומיקום - מספיק כדי לראות סדר.

    ל-conftest יש כבר TEST-001 (מחיר 200 לפני מע"מ, מלאי 4), והוא
    חלק מהציפיות כאן: הטבלה מציגה את כל הקטלוג, לא רק את מה שהוסף.
    """
    with app.app_context():
        organization = Organization.query.filter_by(slug="fixture-org").first()
        rows = [
            ("AA-1", "רפידות בלם", "TRW", 100.0, 8, "A-1"),
            ("BB-2", "דיסק בלם", "BOSCH", 300.0, 0, "A-2"),
            ("CC-3", "פילטר שמן", "MANN", 50.0, 25, "B-7"),
        ]
        for number, name, maker, price, qty, location in rows:
            part = Part(
                part_number=number, name_he=name,
                manufacturer=get_or_create_manufacturer(maker),
            )
            part.cross_refs = [
                CrossReference(ref_number=f"OEM-{number}", ref_type="OEM")
            ]
            db.session.add(part)
            db.session.flush()
            db.session.add(
                OrgPart(
                    organization=organization, part=part, price=price,
                    vat_included=True, stock_qty=qty, min_stock=5, location=location,
                )
            )
        db.session.commit()
        yield organization


@pytest.fixture
def admin_client(app, client):
    """מנהל אפליקציה - הוא היחיד שקובע את הפריסה."""
    app.config["SUPERADMIN_EMAILS"] = frozenset({SUPERADMIN})
    with app.app_context():
        organization = Organization.query.filter_by(slug="fixture-org").first()
        db.session.add(
            User(phone=SUPERADMIN_PHONE, email=SUPERADMIN, role="owner",
                 organization=organization)
        )
        db.session.commit()
    client.post("/logout")
    client.post("/login", data={"phone": SUPERADMIN_PHONE})
    return client


def numbers(response):
    """המק"טים לפי סדר הופעתם בטבלה."""
    html = response.get_data(as_text=True)
    body = html.split("<tbody>")[1].split("</tbody>")[0]
    return [
        line.split(">")[-1]
        for line in body.split('class="font-monospace fw-bold sku"')[1:]
        for line in [line.split("</a>")[0]]
    ]


# ---------- הרישום ----------

def test_every_column_declares_what_it_can_do():
    """עמודה בלי כותרת, או עם מפתח כפול, היא טבלה שבורה."""
    keys = [column.key for column in part_columns.COLUMNS]
    assert len(keys) == len(set(keys))
    for column in part_columns.COLUMNS:
        assert column.label
        assert column.sortable or column.filterable, column.key


def test_the_default_layout_is_the_table_that_was_here_before(client, catalog):
    html = client.get("/parts").get_data(as_text=True)
    header = html.split("<thead")[1].split("</thead>")[0]
    for label in ["ט מקורי", "מתאים ל", "מלאי"]:
        assert label in header, label
    assert "מיקום במחסן" not in header      # קיימת ברישום, לא בברירת המחדל


def test_an_unknown_key_in_a_saved_layout_does_not_break_the_table(app, client, catalog):
    """פריסה שמצביעה על עמודה שהוסרה מהקוד - הטבלה עדיין נטענת."""
    with app.app_context():
        services.save_column_layout(["part_number", "gone_away", "stock"])
    response = client.get("/parts")
    assert response.status_code == 200
    header = response.get_data(as_text=True).split("<thead")[1].split("</thead>")[0]
    assert "מלאי" in header


def test_a_layout_of_only_unknown_keys_falls_back_to_the_default(app, client, catalog):
    with app.app_context():
        services.save_column_layout(["nothing_here"])
        assert [c.key for c in services.column_layout()] == list(part_columns.DEFAULT_KEYS)


# ---------- מיון ----------

@pytest.mark.parametrize("sort,expected", [
    # מחירים כולל מע"מ: CC-3 50, AA-1 100, TEST-001 236, BB-2 300
    ("price:asc", ["CC-3", "AA-1", "TEST-001", "BB-2"]),
    ("price:desc", ["BB-2", "TEST-001", "AA-1", "CC-3"]),
    # מלאי: BB-2 0, TEST-001 4, AA-1 8, CC-3 25
    ("stock:asc", ["BB-2", "TEST-001", "AA-1", "CC-3"]),
    ("stock:desc", ["CC-3", "AA-1", "TEST-001", "BB-2"]),
    ("part_number:desc", ["TEST-001", "CC-3", "BB-2", "AA-1"]),
])
def test_each_column_sorts_both_ways(client, catalog, sort, expected):
    assert numbers(client.get(f"/parts?sort={sort}")) == expected


def test_the_price_sort_follows_what_the_column_shows(app, client, catalog):
    """המחיר בעמודה כולל מע"מ, ולכן המיון חייב להיות עליו ולא על הגולמי.

    שני המק"טים כאן נבחרו כדי שהסדר יתהפך: לפי המחיר הגולמי TEST-001
    זול יותר (100 מול 110), ולפי מה שכתוב בעמודה הוא יקר יותר (118
    מול 110), כי הוא היחיד שהמע"מ עוד לא בתוכו.
    """
    with app.app_context():
        for number, price, included in [("AA-1", 110.0, True), ("TEST-001", 100.0, False)]:
            link = Part.query.filter_by(part_number=number).first().org_links[0]
            link.price, link.vat_included = price, included
        db.session.commit()
    assert numbers(client.get("/parts?sort=price:asc")) == [
        "CC-3", "AA-1", "TEST-001", "BB-2"]


def test_the_old_sort_names_still_mean_what_they_meant(client, catalog):
    """קישור ישן עם sort=stock פירושו מלאי יורד, ולא עמודה בשם הזה."""
    assert numbers(client.get("/parts?sort=stock")) == [
        "CC-3", "AA-1", "TEST-001", "BB-2"]


def test_an_unknown_sort_does_not_break_anything(client, catalog):
    assert client.get("/parts?sort=nonsense:desc").status_code == 200


def test_the_sorted_column_says_so_in_the_header(client, catalog):
    html = client.get("/parts?sort=stock:desc").get_data(as_text=True)
    assert "ממוין יורד" in html
    assert "sort=stock%3Aasc" in html or "sort=stock:asc" in html   # לחיצה הופכת


# ---------- סינון ----------

def test_text_columns_filter_by_what_they_contain(client, catalog):
    assert numbers(client.get("/parts?f_name_he=פילטר")) == ["CC-3"]
    assert numbers(client.get("/parts?f_part_number=BB")) == ["BB-2"]


def test_the_original_number_is_searchable_too(client, catalog):
    """מק"ט מקורי יושב בטבלה אחרת, והסינון בכל זאת מגיע אליו."""
    assert numbers(client.get("/parts?f_oem=OEM-CC")) == ["CC-3"]


@pytest.mark.parametrize("expression,expected", [
    (">5", ["AA-1", "CC-3"]),
    (">=25", ["CC-3"]),
    ("<1", ["BB-2"]),
    ("8", ["AA-1"]),
    ("0-8", ["AA-1", "BB-2", "TEST-001"]),
])
def test_number_columns_take_comparisons_and_ranges(client, catalog, expression, expected):
    assert sorted(numbers(client.get(f"/parts?f_stock={expression}"))) == expected


def test_a_filter_that_is_not_a_number_filters_nothing(client, catalog):
    """מסנן שהוקלד למחצה לא אמור לרוקן את הטבלה."""
    assert len(numbers(client.get("/parts?f_stock=לא מספר"))) == 4


def test_a_select_column_filters_by_the_chosen_value(app, client, catalog):
    with app.app_context():
        maker_id = get_or_create_manufacturer("BOSCH").id
    assert numbers(client.get(f"/parts?f_manufacturer={maker_id}")) == ["BB-2"]


def test_filters_and_sort_live_together(client, catalog):
    assert numbers(client.get("/parts?f_stock=>0&sort=price:desc")) == [
        "TEST-001", "AA-1", "CC-3"]


def test_a_column_filter_marks_the_screen_as_filtered(client, catalog):
    """הסרגל נפתח מעצמו כשמשהו מסונן, גם כשהסינון הגיע מהכותרת."""
    html = client.get("/parts?f_name_he=פילטר").get_data(as_text=True)
    assert "מתוך" in html                    # "1 מתוך 4"


# ---------- הרכב שהחלק מתאים לו, וכמה יש ממנו ----------

@pytest.fixture
def vehicles(app, catalog):
    """שני מק"טים נוספים לאותה קורולה, ואחד שהוא תחליף מוצהר.

    AA-1 ו-CC-3 מהקטלוג מקבלים התאמה לקורולה, וכך יש לרכב הזה שלושה
    חלפים במאגר (יחד עם TEST-001 של ה-fixture).
    """
    from app.models import CrossReference, Fitment

    with app.app_context():
        for number in ["AA-1", "CC-3"]:
            part = Part.query.filter_by(part_number=number).first()
            db.session.add(Fitment(part=part, make="טויוטה", model="COROLLA",
                                   year_from=2015))
        # BB-2 מצהיר על המק"ט של AA-1 כמקביל שלו: לפי הכלל של
        # equivalent_parts הם תחליפים זה של זה, לשני הכיוונים
        bb = Part.query.filter_by(part_number="BB-2").first()
        db.session.add(CrossReference(part=bb, ref_number="AA-1", ref_type="OEM"))
        db.session.commit()
        yield


def test_the_new_columns_are_on_the_shelf(client):
    labels = {c.key: c.label for c in part_columns.COLUMNS}
    assert labels["vehicle_make"] == "יצרן רכב"
    assert labels["vehicle_model"] == "דגם רכב"
    assert labels["catalog_parts"] == "חלפים במאגר"
    assert labels["substitutes"] == "תחליפים"


def test_the_vehicle_columns_show_make_and_model(app, client, vehicles):
    with app.app_context():
        services.save_column_layout(["part_number", "vehicle_make", "vehicle_model"])
    html = client.get("/parts?f_part_number=TEST").get_data(as_text=True)
    body = html.split("<tbody>")[1].split("</tbody>")[0]
    assert "טויוטה" in body
    assert "COROLLA" in body


@pytest.mark.parametrize("param,value,expected", [
    ("f_vehicle_make", "טויוטה", ["AA-1", "CC-3", "TEST-001"]),
    ("f_vehicle_model", "COROLLA", ["AA-1", "CC-3", "TEST-001"]),
    ("f_vehicle_model", "אין דגם כזה", []),
])
def test_the_vehicle_columns_filter(client, vehicles, param, value, expected):
    assert sorted(numbers(client.get(f"/parts?{param}={value}"))) == expected


def test_the_catalog_count_is_the_same_number_the_stats_screen_shows(app, vehicles):
    """"חלפים במאגר" הוא מונח קיים במערכת - מק"טים בקטלוג שמתאימים לדגם.
    המספר בעמודה חייב להיות אותו מספר, אחרת שני המסכים סותרים זה את זה."""
    with app.app_context():
        parts = Part.query.filter(Part.part_number.in_(["AA-1", "CC-3", "TEST-001"])).all()
        counts = services.column_counts(parts, part_columns.COLUMNS)
        from_service = services.vehicle_part_counts("טויוטה", "COROLLA")[0]
        assert from_service == 3
        for part in parts:
            assert counts[part.id]["catalog_parts"] == 3


def test_the_substitute_count_matches_the_list_on_the_part_card(app, vehicles):
    """אותו כלל בדיוק כמו "מק"טים שקולים בקטלוג" שבכרטיס המק"ט.

    כולל אי-הסימטריה שבו: BB-2 הצהיר על המק"ט של AA-1 כמקביל שלו,
    ולכן AA-1 רואה את BB-2 כתחליף - אבל לא להפך. הכלל מחפש את
    *המספרים שלי* אצל *המקבילים של האחרים*, ולא את המספרים שלהם אצלי.
    העמודה מצטטת את הכלל הזה ולא ממציאה אחר, אחרת המספר בתא היה סותר
    את הרשימה שנפתחת בלחיצה על המק"ט.
    """
    with app.app_context():
        for number in ["AA-1", "BB-2", "CC-3"]:
            part = Part.query.filter_by(part_number=number).first()
            counts = services.column_counts([part], part_columns.COLUMNS)
            assert counts[part.id]["substitutes"] == len(services.equivalent_parts(part)), number
        aa = Part.query.filter_by(part_number="AA-1").first()
        assert services.column_counts([aa], part_columns.COLUMNS)[aa.id]["substitutes"] == 1


def test_a_part_with_nothing_attached_counts_zero(app, client, catalog):
    with app.app_context():
        db.session.add(Part(part_number="LONE-1", name_he="בודד"))
        db.session.commit()
        part = Part.query.filter_by(part_number="LONE-1").first()
        counts = services.column_counts([part], part_columns.COLUMNS)
        assert counts[part.id]["catalog_parts"] == 0
        assert counts[part.id]["substitutes"] == 0


@pytest.mark.parametrize("sort", [
    "vehicle_make:asc", "vehicle_make:desc",
    "vehicle_model:asc", "vehicle_model:desc",
    "catalog_parts:desc", "substitutes:desc",
])
def test_the_new_columns_sort(client, vehicles, sort):
    assert len(numbers(client.get(f"/parts?sort={sort}"))) == 4


def test_the_counts_sort_by_the_number_they_show(client, app, vehicles):
    """המק"טים של הקורולה (3 חלפים במאגר) לפני זה שאין לו רכב משלו."""
    with app.app_context():
        services.save_column_layout(["part_number", "catalog_parts"])
    assert numbers(client.get("/parts?sort=catalog_parts:asc"))[0] == "BB-2"


def test_the_counts_filter_as_numbers(client, vehicles):
    assert sorted(numbers(client.get("/parts?f_catalog_parts=%3E2"))) == [
        "AA-1", "CC-3", "TEST-001"]
    # רק AA-1: הוא זה שמישהו אחר הצהיר על המספר שלו (ראה אי-הסימטריה למעלה)
    assert sorted(numbers(client.get("/parts?f_substitutes=%3E0"))) == ["AA-1"]


def test_the_counts_are_fetched_in_one_query_for_the_whole_page(app, client, vehicles):
    """שאילתה לכל שורה הייתה 25 שאילתות לדף. וכשהעמודות אינן מוצגות -
    אין שאילתה בכלל."""
    with app.app_context():
        parts = Part.query.all()
        plain = [c for c in services.column_layout() if c.key != "catalog_parts"]
        assert services.column_counts(parts, plain) == {}
        assert len(services.column_counts(parts, part_columns.COLUMNS)) == len(parts)


# ---------- חשיבות החלק: הצי שמאחורי הרכב ----------

@pytest.fixture
def fleet(app, vehicles):
    """צילום מרשם: קורולה נפוצה, ריו נדירה, ורכב שאין לו חלפים בקטלוג.

    שמות היצרן והדגם נכתבים כמו במרשם ולא כמו בקטלוג - "טויוטה יפן"
    מול "טויוטה", ו-"COROLLA HSD SDN" מול "COROLLA" - כי זה בדיוק
    הפער שההצלבה קיימת בשבילו.
    """
    from datetime import datetime, timezone

    from app.fleet_stats import FleetModelCount, forget_fleet_index

    # חותמת אחת לכל השורות: צילום הוא רגע אחד, וזה גם מה שמזהה אותו
    taken_at = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    with app.app_context():
        forget_fleet_index()
        db.session.add_all([
            FleetModelCount(make="טויוטה יפן", model="COROLLA HSD SDN", taken_at=taken_at,
                            vehicles=90000, young=1000, prime=60000, old=29000),
            FleetModelCount(make="טויוטה יפן", model="COROLLA", taken_at=taken_at,
                            vehicles=30000, young=500, prime=20000, old=9500),
            FleetModelCount(make="קיה קוריאה", model="RIO", taken_at=taken_at,
                            vehicles=4000, young=100, prime=2000, old=1900),
        ])
        db.session.commit()
        yield
        forget_fleet_index()


def test_the_fleet_columns_are_on_the_shelf(client):
    labels = {c.key: c.label for c in part_columns.COLUMNS}
    assert labels["fleet_vehicles"] == "רכבים על הכביש"
    assert labels["fleet_prime"] == "בטווח הקנייה"
    assert labels["fleet_gap"] == 'רכבים למק"ט'


def test_the_registry_spelling_is_bridged_to_the_catalog(app, fleet):
    """המרשם כותב "טויוטה יפן" ו-"COROLLA HSD SDN"; הקטלוג "טויוטה"
    ו-"COROLLA". שתי שורות המרשם נספרות יחד לאותו רכב בקטלוג."""
    from app import fleet_stats

    with app.app_context():
        row = fleet_stats.catalog_fleet_numbers()[("טויוטה", "COROLLA")]
        assert row["vehicles"] == 120000
        assert row["prime"] == 80000


def test_the_cell_shows_the_fleet_behind_the_part(app, client, fleet):
    with app.app_context():
        parts = Part.query.filter(Part.part_number.in_(["AA-1", "BB-2"])).all()
        counts = services.column_counts(parts, part_columns.COLUMNS)
        by_number = {p.part_number: counts[p.id] for p in parts}
    assert by_number["AA-1"]["fleet_vehicles"] == 120000     # קורולה
    assert by_number["AA-1"]["fleet_prime"] == 80000
    assert by_number["BB-2"]["fleet_vehicles"] == 0          # אין לו רכב במרשם


def test_the_gap_is_prime_over_the_parts_we_have(app, client, fleet):
    """אותו מדד של מסך הצי: רכבים בטווח הקנייה חלקי מק"טים לדגם."""
    with app.app_context():
        part = Part.query.filter_by(part_number="AA-1").first()
        counts = services.column_counts([part], part_columns.COLUMNS)
        catalog_parts = services.vehicle_part_counts("טויוטה", "COROLLA")[0]
        assert catalog_parts == 3
        assert counts[part.id]["fleet_gap"] == pytest.approx(80000 / 3)


def test_sorting_by_the_fleet_agrees_with_the_cell(app, client, fleet):
    """הסדר נבנה ב-SQL והתא בפייתון, ושניהם מאותה מפה - אסור שיסתרו."""
    with app.app_context():
        services.save_column_layout(["part_number", "fleet_vehicles"])
    order = numbers(client.get("/parts?sort=fleet_vehicles:desc"))
    assert order[0] in ("AA-1", "CC-3", "TEST-001")      # כולם קורולה
    assert order[-1] == "BB-2"                           # ואין לו רכב במרשם

    with app.app_context():
        parts = {p.part_number: p for p in Part.query.all()}
        counts = services.column_counts(list(parts.values()), part_columns.COLUMNS)
        by_number = {n: counts[p.id]["fleet_vehicles"] for n, p in parts.items()}
    assert [by_number[n] for n in order] == sorted(
        (by_number[n] for n in order), reverse=True)


def test_the_gap_agrees_with_the_cell_for_a_part_that_fits_two_cars(app, client, fleet):
    """המלכודת: "הרכבים הגדולים חלקי המק"טים הרבים" אינו "הפער הגדול".

    AA-1 מתאים גם לקורולה (80,000 בטווח / 3 מק"טים) וגם לריו
    (2,000 / 1 מק"ט). הפער הנכון הוא הגדול מבין השניים, ולא חלוקה של
    המקסימומים זה בזה. התא והמיון חייבים לומר את אותו דבר.
    """
    from app.models import Fitment

    with app.app_context():
        aa = Part.query.filter_by(part_number="AA-1").first()
        db.session.add(Fitment(part=aa, make="קיה", model="RIO", year_from=2016))
        db.session.commit()
        services.save_column_layout(["part_number", "fleet_gap"])

        part = Part.query.filter_by(part_number="AA-1").first()
        cell = services.column_counts([part], part_columns.COLUMNS)[part.id]["fleet_gap"]
        corolla = 80000 / services.vehicle_part_counts("טויוטה", "COROLLA")[0]
        rio = 2000 / services.vehicle_part_counts("קיה", "RIO")[0]
        assert cell == pytest.approx(max(corolla, rio))
        # ולא החלוקה של המקסימומים, שהיא מספר אחר לגמרי
        assert cell != pytest.approx(80000 / services.vehicle_part_counts("קיה", "RIO")[0])

        # והמיון, שנבנה ב-SQL, מסכים עם התא
        parts = Part.query.all()
        by_id = services.column_counts(parts, part_columns.COLUMNS)
        expected = sorted(
            (p.part_number for p in parts),
            key=lambda n: -by_id[
                next(p.id for p in parts if p.part_number == n)]["fleet_gap"],
        )
    assert numbers(client.get("/parts?sort=fleet_gap:desc"))[0] == expected[0]


def test_the_fleet_columns_filter_as_numbers(client, fleet):
    assert sorted(numbers(client.get("/parts?f_fleet_vehicles=%3E1000"))) == [
        "AA-1", "CC-3", "TEST-001"]
    assert numbers(client.get("/parts?f_fleet_prime=%3E100000")) == []


def test_without_a_fleet_snapshot_the_cell_says_unknown_not_zero(app, client, vehicles):
    """"לא נטענה ספירה" אינו "אין רכבים כאלה"."""
    with app.app_context():
        part = Part.query.filter_by(part_number="AA-1").first()
        counts = services.column_counts([part], part_columns.COLUMNS)
        assert counts[part.id]["fleet_vehicles"] is None
        services.save_column_layout(["part_number", "fleet_vehicles"])
    html = client.get("/parts").get_data(as_text=True)
    assert "לא נטענה ספירת צי" in html


def test_without_a_snapshot_sorting_still_works(client, vehicles):
    """בלי צילום אין CASE לבנות ממנו, והמיון לא אמור להתפוצץ."""
    assert client.get("/parts?sort=fleet_gap:desc").status_code == 200
    assert client.get("/parts?f_fleet_vehicles=%3E0").status_code == 200


def test_a_new_snapshot_is_not_served_from_the_old_memory(app, fleet):
    """הצי המנורמל נשמר בזיכרון לפי חותמת הצילום; צילום חדש פוסל אותו."""
    from app import fleet_stats
    from app.fleet_stats import FleetModelCount

    with app.app_context():
        assert fleet_stats.catalog_fleet_numbers()[("טויוטה", "COROLLA")]["vehicles"] == 120000
        fleet_stats.replace_snapshot([{
            "make": "טויוטה יפן", "model": "COROLLA", "model_code": "1",
            "vehicles": 7, "young": 1, "prime": 5, "old": 1,
            "year_from": 2015, "year_to": 2020,
        }])
        assert fleet_stats.catalog_fleet_numbers()[("טויוטה", "COROLLA")]["vehicles"] == 7


# ---------- מה ש-SQLite סלח עליו ו-Postgres לא ----------

def test_the_grouped_expression_is_written_the_same_in_select_and_group_by():
    """Postgres משווה SELECT מול GROUP BY לפי טקסט הביטוי.

    כשהרווח והמקף נשלחים כפרמטרים, אותו ביטוי מקבל מספרים שונים בשני
    המקומות ($1 מול $9), ו-Postgres טוען שהעמודה אינה מקובצת ומסרב.
    SQLite לא אמר כלום, והמסך נפל רק בפרודקשן. הבדיקה הזאת קוראת את
    ה-SQL שנוצר ומוודאת ששני המקומות כתובים אותו דבר.
    """
    from sqlalchemy.dialects import postgresql

    sql = str(
        part_columns._PARTS_PER_VEHICLE.original.compile(dialect=postgresql.dialect())
    )
    selected, _, grouped = sql.partition("GROUP BY")
    squashed = "replace(replace(lower(fitments.make), ' ', ''), '-', '')"
    assert squashed in selected
    assert squashed in grouped
    assert "%(replace_" not in sql          # אין פרמטרים בביטוי הכיווץ


@pytest.mark.parametrize("expression", ["CATALOG_PARTS", "SUBSTITUTES"])
def test_a_count_of_nothing_is_zero_and_not_null(expression):
    """NULL ממוין ראשון ב-SQLite ואחרון ב-Postgres, והתא מציג 0.

    בלי coalesce אותה לחיצה הייתה נותנת שתי תשובות שונות לפי בסיס
    הנתונים, ושתיהן סותרות את מה שכתוב במסך.
    """
    from sqlalchemy.dialects import postgresql

    sql = str(
        getattr(part_columns, expression).compile(dialect=postgresql.dialect())
    )
    assert sql.lstrip().lower().startswith("coalesce")


# ---------- הכותרת: שני אייקונים, בלי שורה נוספת ----------

def test_the_header_is_a_single_row(client, catalog):
    """הסינון יושב באייקון שליד הכותרת, ולא בשורה משלו מתחתיה."""
    head = client.get("/parts").get_data(as_text=True).split("<thead")[1].split("</thead>")[0]
    assert head.count("<tr") == 1


def test_each_column_carries_its_two_icons(client, catalog):
    head = client.get("/parts").get_data(as_text=True).split("<thead")[1].split("</thead>")[0]
    sortable = [c for c in services.column_layout() if c.sortable]
    filterable = [c for c in services.column_layout() if c.filterable]
    assert head.count('class="col-sort"') == len(sortable)
    assert head.count("col-filter") >= len(filterable)
    # אייקונים מוטבעים, לא פונט מ-CDN: הטבלה נראית אותו דבר גם בלי רשת
    assert "<svg" in head


def test_a_column_that_cannot_be_sorted_has_no_sort_icon(client, app, catalog):
    """להתאמות אין סדר אחד נכון, ואייקון מיון שם היה משקר."""
    with app.app_context():
        services.save_column_layout(["fitments", "part_number"])
    head = client.get("/parts").get_data(as_text=True).split("<thead")[1].split("</thead>")[0]
    assert head.count('class="col-sort"') == 1      # רק המק"ט


def test_the_filter_field_opens_by_itself_when_that_column_is_filtered(client, catalog):
    """אחרת הערך שסונן היה נעלם מהעין ברגע שהדף נטען מחדש."""
    head = client.get("/parts").get_data(as_text=True).split("<thead")[1].split("</thead>")[0]
    assert "<details class=\"col-filter\" open>" not in head    # במנוחה הכל סגור

    head = client.get("/parts?f_name_he=פילטר").get_data(as_text=True)
    head = head.split("<thead")[1].split("</thead>")[0]
    assert head.count("open>") == 1


def test_the_filter_fields_belong_to_the_filter_form(client, catalog):
    """טופס אינו יכול לעטוף שורת טבלה, ולכן השדות מקושרים אליו בשמו."""
    html = client.get("/parts").get_data(as_text=True)
    assert 'form="filters"' in html
    assert 'name="f_name_he"' in html


# ---------- חיווי חישוב ----------

def test_the_table_says_when_it_is_recomputing(client, catalog):
    """מיון וסינון הם בקשה לשרת; בלי חיווי המסך נראה תקוע."""
    html = client.get("/parts").get_data(as_text=True)
    assert "data-busy-table" in html
    assert 'id="table-busy"' in html
    # והפס מתחיל מוסתר - הוא מופיע רק בלחיצה
    assert 'id="table-busy" hidden' in html


def test_everything_that_recomputes_the_table_is_marked(client, catalog):
    """מיון, סינון ועימוד - כולם מחזירים דף חדש, וכולם מדליקים את הפס."""
    html = client.get("/parts").get_data(as_text=True)
    head = html.split("<thead")[1].split("</thead>")[0]
    assert head.count("data-table-nav") == len(
        [c for c in services.column_layout() if c.sortable]
    )
    body = client.get("/static/js/app.js").get_data(as_text=True)
    assert "data-busy-table" in body
    assert "table-is-busy" in body
    assert 'aria-busy' in body


# ---------- מי קובע ----------

def test_only_the_app_admin_reaches_the_columns_screen(client, catalog):
    assert client.get("/admin/columns").status_code == 403
    assert client.post("/admin/columns", data={"action": "reset"}).status_code == 403


def test_the_admin_can_add_a_column(admin_client, app, catalog):
    admin_client.post("/admin/columns", data={
        "key": list(part_columns.DEFAULT_KEYS), "action": "add:location"})
    with app.app_context():
        assert "location" in [c.key for c in services.column_layout()]
    assert "מיקום במחסן" in admin_client.get("/parts").get_data(as_text=True)


def test_the_admin_can_remove_a_column(admin_client, app, catalog):
    admin_client.post("/admin/columns", data={
        "key": list(part_columns.DEFAULT_KEYS), "action": "remove:oem"})
    with app.app_context():
        assert "oem" not in [c.key for c in services.column_layout()]


def test_the_admin_can_move_a_column(admin_client, app, catalog):
    admin_client.post("/admin/columns", data={
        "key": ["part_number", "name_he", "stock"], "action": "up:stock"})
    with app.app_context():
        assert [c.key for c in services.column_layout()] == [
            "part_number", "stock", "name_he"]


def test_moving_past_the_edge_changes_nothing(admin_client, app, catalog):
    admin_client.post("/admin/columns", data={
        "key": ["part_number", "name_he"], "action": "up:part_number"})
    with app.app_context():
        assert [c.key for c in services.column_layout()] == ["part_number", "name_he"]


def test_the_table_cannot_be_emptied(admin_client, app, catalog):
    admin_client.post("/admin/columns", data={"key": ["stock"], "action": "remove:stock"})
    with app.app_context():
        assert services.column_layout()          # לא ריק


def test_reset_brings_back_the_default(admin_client, app, catalog):
    admin_client.post("/admin/columns", data={"key": ["stock"], "action": "add:cost"})
    admin_client.post("/admin/columns", data={"key": ["stock", "cost"], "action": "reset"})
    with app.app_context():
        assert [c.key for c in services.column_layout()] == list(part_columns.DEFAULT_KEYS)


def test_the_layout_is_one_for_everyone(admin_client, app, client, catalog):
    """מנהל האפליקציה קובע, וכל המשתמשים רואים - זו לא העדפה אישית."""
    admin_client.post("/admin/columns", data={
        "key": list(part_columns.DEFAULT_KEYS), "action": "add:cost"})
    with app.app_context():
        assert TableLayout.query.count() == 1
        assert TableLayout.query.first().table_key == "parts"
    # אותו לקוח, אחרי שהזדהה מחדש כמשתמש רגיל
    client.post("/logout")
    client.post("/login", data={"phone": "0500000001"})
    assert "עלות" in client.get("/parts").get_data(as_text=True)
