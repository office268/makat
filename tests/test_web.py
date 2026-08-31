"""מסלולי HTTP ו-API."""


def test_the_screens_render(client):
    for route in ["/", "/dashboard", "/parts", "/vehicles", "/categories",
                  "/manufacturers", "/suppliers", "/import", "/parts/new"]:
        assert client.get(route).status_code == 200, route


def test_every_screen_requires_identification(visitor):
    """בלי הזדהות אין מסך - כולם מפנים לשדה הטלפון."""
    for route in ["/dashboard", "/parts", "/vehicles", "/categories",
                  "/manufacturers", "/suppliers", "/import", "/parts/new"]:
        response = visitor.get(route)
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
    assert "TEST-001" not in html                # החיפוש לא רץ
    assert 'מק"טים מתאימים' not in html


def test_identified_vehicle_sits_under_step_one(app, client):
    """הרכב שזוהה שייך לשלב שיצר אותו, ולכן הוא מוצג לפני שלב 2."""
    app.config["SHOW_PART_STEP"] = True
    for action in ("vehicle", "part"):
        html = client.post("/", data={"plate": "12345678", "action": action,
                                      "query": "רפידות קדמיות"}).get_data(as_text=True)
        assert html.index("הרכב שזוהה") < html.index("2. החלק"), action
        assert html.index("1. מספר רישוי") < html.index("הרכב שזוהה"), action
        assert html.count("הרכב שזוהה") == 1, action


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


def test_both_buttons_share_one_form(app, client):
    """שדה מספר הרישוי משותף, ולכן אין שדה מוסתר שיכול להתיישן."""
    app.config["SHOW_PART_STEP"] = True
    html = client.get("/").get_data(as_text=True)
    assert 'name="action" value="vehicle"' in html
    assert 'name="action" value="part"' in html
    assert html.count('name="plate"') == 1


def test_buttons_carry_a_waiting_label(app, client):
    """זיהוי הרכב פונה למאגר חיצוני - הכפתור חייב להראות שמשהו קורה."""
    app.config["SHOW_PART_STEP"] = True
    html = client.get("/").get_data(as_text=True)
    assert "data-busy-form" in html
    assert 'data-busy-label="מזהה רכב..."' in html
    assert "data-busy-label" in html.split('value="part"')[1][:200]


def test_vehicle_card_drops_source_and_duplicate_plate(client):
    """המקור הוא פרט פנימי, ומספר הרישוי כבר מוצג בשדה של שלב 1."""
    html = client.post("/", data={"plate": "12345678",
                                  "action": "vehicle"}).get_data(as_text=True)
    assert "COROLLA" in html
    assert "data.gov.il" not in html
    assert "plate-badge" not in html
    # פעם אחת בלבד, בשדה של שלב 1 - ושם הוא מעוצב עם מקפים.
    # הספרות הרצופות כן מופיעות בקישורי הכיסוי, וזו כתובת ולא תצוגה.
    assert html.count("123-45-678") == 1
    assert 'value="123-45-678"' in html
    assert 'value="12345678"' not in html


def test_vehicle_card_shows_the_extra_fields_it_has(client):
    """שדות שהמאגר מחזיק ולא הוצגו: קוד דגם ומספר שלדה."""
    html = client.post("/", data={"plate": "12345678",
                                  "action": "vehicle"}).get_data(as_text=True)
    assert "ZRE172L" in html                       # קוד דגם
    assert "DEMO0000000000001" in html             # מספר שלדה
    assert "מספר שלדה" in html


def test_vehicle_card_hides_empty_fields(client):
    """שדה שאין לו ערך לא מופיע כשורה ריקה."""
    html = client.post("/", data={"plate": "12345678",
                                  "action": "vehicle"}).get_data(as_text=True)
    assert "בעלות" not in html          # לא קיים בקובץ הדוגמאות
    assert "צמיג קדמי" not in html


def test_catalog_browsable_without_searching(client):
    """עיון בכל הקטלוג בלי להזין כלום - הרשימה, הספירה, וההתאמות."""
    html = client.get("/parts").get_data(as_text=True)
    assert "TEST-001" in html                 # המק"ט מה-fixture פשוט מוצג
    assert 'קטלוג מק"טים' in html
    assert "מתאים ל" in html                  # לאיזה רכב, לא רק שם
    assert "COROLLA" in html                  # ההתאמה עצמה מוצגת


def test_catalog_shows_the_original_part_number(client):
    """המק"ט המקורי הוא מה שהלקוח מביא מהמוסך, ולכן הוא בטבלה עצמה."""
    html = client.get("/parts").get_data(as_text=True)
    assert 'מק"ט מקורי' in html
    assert "04465-02220" in html          # ה-OEM של המק"ט מה-fixture


def test_home_does_not_offer_the_whole_catalog(client):
    """המסך הראשי הוא זיהוי לפי רכב. הקטלוג המלא נשאר בתפריט."""
    html = client.get("/").get_data(as_text=True)
    assert "עיון בכל הקטלוג" not in html


def test_filters_stay_open_when_a_filter_is_active(client):
    """הסינון מקופל בטלפון, אבל לא כשהמשתמש כבר סינן משהו."""
    plain = client.get("/parts").get_data(as_text=True)
    filtered = client.get("/parts?q=TEST").get_data(as_text=True)
    form_plain = plain.split('id="filters"')[0][-120:]
    form_filtered = filtered.split('id="filters"')[0][-120:]
    assert "show" not in form_plain
    assert "show" in form_filtered


def test_empty_filter_result_offers_a_way_back(client):
    html = client.get("/parts?q=לאקייםבכלל").get_data(as_text=True)
    assert "לא נמצאו מק\"טים לסינון הזה" in html
    assert "להצגת כל הקטלוג" in html


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


def test_api_link_is_hidden_before_identification(visitor):
    """הממשק קיים לשילוב במערכות של המוסך, לא כהצעה למי שעומד בדלת.

    מסך ההזדהות הוא המסך היחיד שרואה מי שלא נכנס, ולכן שם נבדק.
    """
    html = visitor.get("/login").get_data(as_text=True)
    assert ">API</a>" not in html
    assert 'קטלוג מק"טים לחלקי רכב' in html      # שאר הכותרת התחתונה נשארה


def test_api_link_shows_for_a_signed_in_user(auth_client):
    assert ">API</a>" in auth_client.get("/").get_data(as_text=True)


def test_vehicle_details_start_folded(client):
    """כרטיס הרכב פותח בזהות ובשלוש עובדות; השאר מאחורי פתיחה אחת."""
    html = client.post("/", data={"plate": "12345678",
                                  "action": "vehicle"}).get_data(as_text=True)
    assert "<details" in html
    assert "כל פרטי הרכב" in html
    assert "COROLLA" in html                       # הזהות מחוץ ל-details
    assert "1ZR-FE" in html                        # קוד מנוע בשורת התקציר


def test_folded_details_still_hold_everything(client):
    """"נגיש" ולא "נמחק": כל שדה שהיה מוצג עדיין בדף."""
    html = client.post("/", data={"plate": "12345678",
                                  "action": "vehicle"}).get_data(as_text=True)
    body = html.split("<details")[1]
    for value in ("ZRE172L", "DEMO0000000000001", "2016"):
        assert value in body, value
    assert "מספר שלדה" in body


def test_coverage_badges_are_links_to_that_part_type(client):
    """לחיצה על "מגב" צריכה להראות את המגבים, ולכן התגית היא קישור."""
    html = client.post("/", data={"plate": "12345678",
                                  "action": "vehicle"}).get_data(as_text=True)
    assert "part_type=brake_pads_front" in html
    assert "plate=12345678" in html
    assert "#results" in html


def test_a_coverage_link_runs_the_search(client):
    """אותה הצטלבות כמו הכפתור, רק דרך הכתובת."""
    response = client.get("/?plate=12345678&part_type=brake_pads_front")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "COROLLA" in html          # הרכב זוהה
    assert "TEST-001" in html         # והחלפים מאותו סוג מוצגים
    assert 'מק"טים מתאימים' in html


def test_the_chosen_type_is_marked_in_the_strip(client):
    html = client.get("/?plate=12345678&part_type=brake_pads_front").get_data(as_text=True)
    strip = html.split("מה קיים בקטלוג לרכב הזה")[1].split("</div>")[0:6]
    assert any("bg-primary" in chunk for chunk in strip)


def test_a_plate_link_without_a_type_only_identifies(client):
    """קישור עם מספר רישוי בלבד הוא שלב 1, לא חיפוש חלק."""
    html = client.get("/?plate=12345678").get_data(as_text=True)
    assert "COROLLA" in html
    assert "TEST-001" not in html
    assert 'מק"טים מתאימים' not in html


def test_the_home_page_without_a_plate_is_unchanged(client):
    html = client.get("/").get_data(as_text=True)
    assert 'name="plate"' in html
    assert "הרכב שזוהה" not in html


def test_part_step_is_hidden_by_default(client):
    """שלב 2 מוסתר כרגע - מוסתר, לא בוטל."""
    html = client.post("/", data={"plate": "12345678",
                                  "action": "vehicle"}).get_data(as_text=True)
    assert "2. החלק" not in html
    assert 'name="action" value="part"' not in html
    assert "או בחירה ידנית" not in html
    # שלב 1 ורצועת הכיסוי - הדרך שנשארה לחלפים - עדיין שם
    assert "1. מספר רישוי" in html
    assert "מה קיים בקטלוג לרכב הזה" in html
    assert "part_type=brake_pads_front" in html


def test_hiding_the_step_did_not_break_the_search_behind_it(client):
    """המסלול לא נגע: קישור הכיסוי וגם POST ישן ממשיכים להחזיר מק"טים."""
    from_link = client.get("/?plate=12345678&part_type=brake_pads_front")
    assert "TEST-001" in from_link.get_data(as_text=True)

    from_post = client.post("/", data={"plate": "12345678", "action": "part",
                                       "query": "רפידות קדמיות"})
    assert "TEST-001" in from_post.get_data(as_text=True)


def test_the_step_comes_back_with_a_flag(app, client):
    """SHOW_PART_STEP=1 מחזיר את שלב 2 בלי שינוי קוד."""
    app.config["SHOW_PART_STEP"] = True
    html = client.get("/").get_data(as_text=True)
    assert "2. החלק" in html
    assert 'name="action" value="part"' in html


def test_a_manual_pick_shows_no_identification_card(client):
    """לחצת על תגית - אין מה "לזהות", וגם לא 100% שמאשר את עצמו."""
    html = client.get("/?plate=12345678&part_type=brake_pads_front").get_data(as_text=True)
    assert "TEST-001" in html               # התוצאות כן
    assert "סוג החלק שזוהה" not in html     # כרטיס הזיהוי לא
    assert "manual" not in html


def test_a_real_identification_still_shows_its_card(app, client):
    """זיהוי מטקסט הוא מידע אמיתי - השיטה ואחוז הביטחון נשארים."""
    app.config["SHOW_PART_STEP"] = True
    html = client.post("/", data={"plate": "12345678", "action": "part",
                                  "query": "רפידות קדמיות"}).get_data(as_text=True)
    assert "סוג החלק שזוהה" in html
    assert "text" in html
    assert "TEST-001" in html


def test_the_plate_field_shows_hyphens(client):
    """8 ספרות -> 107-32-802, גם כשהמשתמש הקליד אותן ברצף."""
    html = client.get("/?plate=10732802").get_data(as_text=True)
    assert 'value="107-32-802"' in html

    seven = client.get("/?plate=1234567").get_data(as_text=True)
    assert 'value="12-345-67"' in seven


def test_a_plate_with_hyphens_still_finds_the_vehicle(client):
    """מה שמוצג חוזר לשרת בשליחה הבאה, ולכן המקפים חייבים לעבור."""
    html = client.post("/", data={"plate": "123-45-678",
                                  "action": "vehicle"}).get_data(as_text=True)
    assert "COROLLA" in html


def test_coverage_badges_show_a_spinner_while_loading(client):
    """הלחיצה פותחת ניווט מלא - בלי חיווי המסך נראה תקוע."""
    html = client.post("/", data={"plate": "12345678",
                                  "action": "vehicle"}).get_data(as_text=True)
    assert "data-busy-link" in html


def test_the_identify_button_is_not_full_width(client):
    """כפתור אחד לפעולה אחת - רוחב לפי התוכן, לא לרוחב הכרטיס."""
    html = client.get("/").get_data(as_text=True)
    block = html.split('name="action" value="vehicle"')[0][-200:]
    assert "d-grid" not in block
    assert "justify-content-center" in block


def test_the_identify_button_carries_an_inline_icon(client):
    """אייקון מוטבע ולא פונט מ-CDN: האפליקציה עובדת גם בלי רשת."""
    html = client.get("/").get_data(as_text=True)
    button = html.split('name="action" value="vehicle"')[1].split("</button>")[0]
    assert "<svg" in button
    assert 'fill="currentColor"' in button       # יורש את צבע הכפתור
    assert 'aria-hidden="true"' in button        # הטקסט הוא השם הנגיש
    assert "זהה רכב" in button


# ---------- הגדרות התצוגה ----------

def test_settings_sit_in_the_menu(client):
    """הסעיף נמצא בתוך התפריט שההמבורגר פותח, ולא במקום אחר."""
    html = client.get("/parts").get_data(as_text=True)
    menu = html.split('id="nav"')[1].split("</nav>")[0]
    assert "הגדרות" in menu
    assert "display-settings" in menu


def test_settings_offer_zoom_and_font_in_both_directions(client):
    html = client.get("/parts").get_data(as_text=True)
    for control in ["zoom:1", "zoom:-1", "font:1", "font:-1"]:
        assert f'data-display-step="{control}"' in html, control
    # קריאה נוכחית לכל אחד מהם, ואיפוס לשניהם יחד
    assert 'data-display-value="zoom"' in html
    assert 'data-display-value="font"' in html
    assert "data-display-reset" in html


def test_the_stepper_buttons_are_labelled_for_a_screen_reader(client):
    """+ ו-- לבדם לא אומרים כלום למי שמקשיב לדף."""
    html = client.get("/parts").get_data(as_text=True)
    for label in ["הגדלת הזום", "הקטנת הזום", "הגדלת הטקסט", "הקטנת הטקסט"]:
        assert f'aria-label="{label}"' in html, label


def test_the_menu_stays_open_while_stepping(client):
    """לחיצה על + לא סוגרת את התפריט - אחרת כל צעד דורש פתיחה מחדש."""
    html = client.get("/parts").get_data(as_text=True)
    assert 'data-bs-auto-close="outside"' in html


def test_display_settings_load_before_the_page_is_drawn(client):
    """הקובץ ב-head ולא בסוף הדף, אחרת הדף נפתח ברגיל וקופץ לגודלו."""
    html = client.get("/parts").get_data(as_text=True)
    head = html.split("</head>")[0]
    assert "js/display.js" in head


def test_the_display_script_is_served(client):
    response = client.get("/static/js/display.js")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "--app-zoom" in body
    assert "--app-font-scale" in body


def test_the_size_is_a_device_setting_not_an_account_one(client):
    """נשמר במכשיר: אותו אדם על מסך גדול ועל טלפון רוצה גדלים שונים."""
    body = client.get("/static/js/display.js").get_data(as_text=True)
    assert "localStorage" in body
    # ואי אפשר להיתקע: כשל אחסון לא מפיל את המסך
    assert "catch" in body


def test_the_settings_reach_the_screen_before_identification(visitor):
    """מי שמתקשה לקרוא צריך להגדיל *לפני* שהוא מקליד את מספרו."""
    html = visitor.get("/login").get_data(as_text=True)
    assert "js/display.js" in html
    assert 'data-display-step="font:1"' in html
