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
