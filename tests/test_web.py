"""מסלולי HTTP ו-API."""


def test_pages_render(client):
    for route in ["/", "/demo", "/parts", "/vehicles", "/categories",
                  "/manufacturers", "/suppliers", "/import"]:
        assert client.get(route).status_code == 200, route


def test_demo_flow_crosses_vehicle_and_part_type(client):
    response = client.post("/demo", data={"plate": "12345678", "query": "רפידות קדמיות"})
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "COROLLA" in html          # הרכב זוהה
    assert "1ZR-FE" in html           # קוד המנוע נשלף
    assert "TEST-001" in html         # ההצטלבות מצאה את המק"ט


def test_demo_rejects_unknown_plate(client):
    response = client.post("/demo", data={"plate": "00000000", "query": "רפידות"})
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


def test_demo_page_labels_data_as_synthetic(client):
    """הבאנר חייב להופיע - אסור שמישהו יטעה לחשוב שהמק"טים אמיתיים."""
    html = client.get("/demo").get_data(as_text=True)
    assert "נתוני הדגמה" in html
    assert "אינם אמיתיים" in html


def test_real_vehicle_outside_catalog_explains_the_gap(client, monkeypatch):
    """רכב שזוהה אך אין לו כיסוי - הודעה שמפרידה בין הזיהוי לבין הקטלוג."""
    from app.routes import demo as demo_module

    monkeypatch.setattr(
        demo_module.vehicles,
        "lookup",
        lambda plate, allow_offline=True: {
            "plate": "9998887", "plate_display": "99-988-87",
            "make": "ב.מ.וו גרמניה", "model": "X3", "trim": "", "year": 2021,
            "engine_code": "B48B20", "fuel": "בנזין", "color": "", "vin": "",
            "test_valid_until": "", "source": "data.gov.il",
        },
    )
    html = client.post(
        "/demo", data={"plate": "9998887", "query": "רפידות קדמיות"}
    ).get_data(as_text=True)
    assert "הרכב זוהה במלואו" in html      # הזיהוי הצליח
    assert "מחוץ לכיסוי הקטלוג" in html    # והמגבלה מוסברת במפורש
    assert "B48B20" in html                # פרטי הרכב האמיתיים מוצגים
