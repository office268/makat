"""מסלולי HTTP ו-API."""


def test_public_pages_render(client):
    for route in ["/", "/dashboard", "/parts", "/vehicles", "/categories",
                  "/manufacturers", "/suppliers", "/login", "/signup"]:
        assert client.get(route).status_code == 200, route


def test_editing_pages_require_login(client):
    """מסכי העריכה מפנים לדף התחברות במקום להיפתח."""
    for route in ["/import", "/parts/new"]:
        response = client.get(route)
        assert response.status_code == 302, route
        assert "/login" in response.headers["Location"], route


def test_demo_flow_crosses_vehicle_and_part_type(client):
    response = client.post("/", data={"plate": "12345678", "query": "רפידות קדמיות"})
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "COROLLA" in html          # הרכב זוהה
    assert "1ZR-FE" in html           # קוד המנוע נשלף
    assert "TEST-001" in html         # ההצטלבות מצאה את המק"ט


def test_vehicle_button_stops_after_identifying_the_vehicle(client):
    """שלב 1 לבדו: מזהה רכב ולא מריץ חיפוש חלק."""
    response = client.post("/", data={"plate": "12345678", "action": "vehicle",
                                      "query": "רפידות קדמיות"})
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "COROLLA" in html                     # הרכב זוהה
    assert "הרכב זוהה. עכשיו שלב 2." in html      # ההנחיה לשלב הבא
    assert "TEST-001" not in html                # החיפוש לא רץ
    assert 'מק"טים מתאימים' not in html


def test_part_button_runs_the_full_search(client):
    """שלב 2: אותו טופס, כפתור אחר - מזהה רכב ומצליב מול הקטלוג."""
    response = client.post("/", data={"plate": "12345678", "action": "part",
                                      "query": "רפידות קדמיות"})
    html = response.get_data(as_text=True)
    assert "COROLLA" in html
    assert "TEST-001" in html
    assert "הרכב זוהה. עכשיו שלב 2." not in html


def test_part_search_without_a_part_says_so(client):
    """כפתור החלק בלי לתאר חלק - הודעה מפורשת, לא מסך שקט."""
    response = client.post("/", data={"plate": "12345678", "action": "part"})
    html = response.get_data(as_text=True)
    assert "יש לתאר את החלק" in html
    assert "COROLLA" in html          # הרכב עדיין מוצג, לא מאבדים את השלב הראשון


def test_both_buttons_share_one_form(client):
    """שדה מספר הרישוי משותף, ולכן אין שדה מוסתר שיכול להתיישן."""
    html = client.get("/").get_data(as_text=True)
    assert 'name="action" value="vehicle"' in html
    assert 'name="action" value="part"' in html
    assert html.count('name="plate"') == 1


def test_demo_rejects_unknown_plate(client):
    response = client.post("/", data={"plate": "00000000", "query": "רפידות"})
    assert "לא נמצא רכב" in response.get_data(as_text=True)


def test_api_identify_returns_vehicle_and_matches(client):
    response = client.post("/api/identify",
                           data={"plate": "12345678", "query": "רפידות קדמיות"})
    payload = response.get_json()
    assert payload["vehicle"]["model"] == "COROLLA"
    assert payload["candidates"][0]["part_type"] == "brake_pads_front"
    assert [m["part_number"] for m in payload["matches"]] == ["TEST-001"]


def test_api_vehicle_not_found(client):
    assert client.get("/api/vehicle/00000000").status_code == 404


def test_lookup_by_oem_cross_reference(client):
    """חיפוש לפי מק"ט מקורי חייב להגיע לחלק החלופי."""
    response = client.get("/parts/lookup?number=04465-02220", follow_redirects=True)
    assert "TEST-001" in response.get_data(as_text=True)


def test_export_csv_has_bom_and_header(client):
    response = client.get("/export.csv")
    text = response.get_data(as_text=True)
    assert text.startswith("﻿")       # BOM כדי שאקסל יציג עברית
    assert "part_number" in text
    assert "TEST-001" in text


def test_part_form_accepts_structured_fitment_rows(auth_client, app, org_id):
    """התאמות לרכב מוזנות בשדות נפרדים, לא כמחרוזת עם נקודתיים."""
    from app.models import Part

    auth_client.post("/parts/new", data={
        "part_number": "ROW-1", "name_he": "דיסק בלם",
        "fit_make": ["טויוטה", "לקסוס"],
        "fit_model": ["COROLLA", "CT200H"],
        "fit_year_from": ["2013", "2011"],
        "fit_year_to": ["2018", "2017"],
        "fit_engine": ["1ZR-FE", "2ZR-FXE"],
        "cross_ref_number": ["04465-02220"],
        "cross_ref_type": ["OEM"],
        "cross_ref_brand": ["Toyota"],
    })
    with app.app_context():
        part = Part.query.filter_by(part_number="ROW-1").first()
        assert part is not None
        assert len(part.fitments) == 2
        assert {f.make for f in part.fitments} == {"טויוטה", "לקסוס"}
        corolla = next(f for f in part.fitments if f.model == "COROLLA")
        assert (corolla.year_from, corolla.year_to) == (2013, 2018)
        assert corolla.engine_code == "1ZR-FE"
        assert part.cross_refs[0].ref_number == "04465-02220"


def test_empty_fitment_rows_are_skipped(auth_client, app):
    """שורה ריקה בטופס לא יוצרת התאמה ריקה."""
    from app.models import Part

    auth_client.post("/parts/new", data={
        "part_number": "ROW-2", "name_he": "מסנן",
        "fit_make": ["מאזדה", "", ""],
        "fit_model": ["MAZDA 3", "", ""],
        "cross_ref_number": ["", ""],
    })
    with app.app_context():
        part = Part.query.filter_by(part_number="ROW-2").first()
        assert len(part.fitments) == 1
        assert part.cross_refs == []


def test_csv_import_still_uses_the_string_format(app, org_id):
    """הפורמט הישן חייב להמשיך לעבוד - מחירוני ספקים מגיעים כך."""
    import io

    from app.models import Part
    from app.services import import_csv

    csv_text = (
        "part_number,name_he,fitments,cross_refs\n"
        "CSV-1,רפידות,טויוטה:COROLLA:2013:2018:1ZR-FE,OEM:04465-02220:Toyota\n"
    )
    created, _updated, errors = import_csv(io.StringIO(csv_text), organization_id=org_id)
    assert (created, errors) == (1, [])
    part = Part.query.filter_by(part_number="CSV-1").first()
    assert part.fitments[0].make == "טויוטה"
    assert part.cross_refs[0].ref_number == "04465-02220"


def test_save_and_add_another_returns_to_the_form(auth_client):
    response = auth_client.post("/parts/new", data={
        "part_number": "ROW-3", "name_he": "חלק", "save_and_new": "1"})
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/parts/new")


def test_home_is_the_identify_flow(client):
    """השורש הוא זיהוי לפי מספר רישוי, לא לוח מחוונים."""
    html = client.get("/").get_data(as_text=True)
    assert "מספר רישוי" in html
    assert 'name="plate"' in html


def test_legacy_urls_redirect_home(client):
    """קישורים שכבר נשלחו לא נשברים."""
    for path in ["/demo", "/identify"]:
        response = client.get(path)
        assert response.status_code == 302, path
        assert response.headers["Location"].endswith("/"), path


def test_dashboard_moved_and_still_works(client):
    html = client.get("/dashboard").get_data(as_text=True)
    assert "לוח מחוונים" in html
    assert "יצרני חלקים" in html      # אחד הכרטיסים בלוח


def test_home_stays_usable_with_an_empty_catalog(app, client):
    """מסך הזיהוי עומד בפני עצמו - הטופס נשאר שם גם בלי קטלוג."""
    from scripts.clear_catalog import clear_catalog

    clear_catalog(app)
    html = client.get("/").get_data(as_text=True)
    assert client.get("/").status_code == 200
    assert 'name="plate"' in html
