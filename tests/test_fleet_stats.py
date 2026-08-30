"""ספירת הרכבים הפעילים לפי דגם: משיכה, צבירה, צילום מצב והמסך."""
import json
from contextlib import contextmanager

from app import fleet_stats
from app.fleet_stats import FleetModelCount


def sql_record(make, model, vehicles, code="A1", year_from=2015, year_to=2020):
    return {
        "make": make, "model": model, "model_code": code,
        "vehicles": vehicles, "year_from": year_from, "year_to": year_to,
    }


def raw(make, model, year, code="A1"):
    """רשומת רכב גולמית מהמאגר - שורה אחת לכל רכב פעיל."""
    return {
        fleet_stats.FIELD_MAKE: make,
        fleet_stats.FIELD_MODEL: model,
        fleet_stats.FIELD_CODE: code,
        fleet_stats.FIELD_YEAR: year,
    }


def snapshot(app, rows):
    with app.app_context():
        return fleet_stats.replace_snapshot(rows)


# ---- שכבת הרשת ----


def test_sql_page_builds_group_by_query(monkeypatch):
    captured = {}

    @contextmanager
    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url

        class Response:
            def read(self):
                payload = {"success": True,
                           "result": {"records": [sql_record("טויוטה", "COROLLA", 5)]}}
                return json.dumps(payload).encode("utf-8")

        yield Response()

    monkeypatch.setattr(fleet_stats.urllib.request, "urlopen", fake_urlopen)
    records = fleet_stats.sql_page(0, limit=100, min_count=10)

    assert records[0]["model"] == "COROLLA"
    assert "GROUP+BY" in captured["url"]
    assert "%3E%3D+10" in captured["url"]  # HAVING count(*) >= 10
    assert fleet_stats.RESOURCE_ID in captured["url"]


def test_sql_page_raises_on_ckan_error(monkeypatch):
    """נקודת ה-SQL מחזירה 200 עם success=false. בלי הבדיקה זה נראה כמו אפס דגמים."""

    @contextmanager
    def fake_urlopen(request, timeout=None):
        class Response:
            def read(self):
                return json.dumps({"success": False, "error": "SQL disabled"}).encode()

        yield Response()

    monkeypatch.setattr(fleet_stats.urllib.request, "urlopen", fake_urlopen)
    try:
        fleet_stats.sql_page(0)
    except ValueError as exc:
        assert "SQL disabled" in str(exc)
    else:
        raise AssertionError("שגיאת המאגר נבלעה")


def test_fetch_counts_pages_until_short_page():
    pages = [
        [sql_record("טויוטה", "COROLLA", 90000), sql_record("מאזדה", "3", 60000)],
        [sql_record("קיה", "פיקנטו", 40000)],
    ]

    def fetch(offset, limit, min_count):
        return pages[0] if offset == 0 else pages[1] if offset == 2 else []

    rows = fleet_stats.fetch_counts(page_size=2, fetch=fetch)
    assert [row["model"] for row in rows] == ["COROLLA", "3", "פיקנטו"]
    assert rows[0]["vehicles"] == 90000
    assert rows[0]["year_from"] == 2015


def test_fetch_counts_respects_max_rows():
    def fetch(offset, limit, min_count):
        return [sql_record("טויוטה", f"M{offset + i}", 10) for i in range(limit)]

    assert len(fleet_stats.fetch_counts(page_size=5, fetch=fetch, max_rows=7)) == 7


# ---- צבירה מקומית ----


def test_aggregate_records_counts_per_model():
    counts = fleet_stats.aggregate_records([
        raw("טויוטה", "COROLLA", 2015),
        raw("טויוטה", "COROLLA", 2019),
        raw("טויוטה", "COROLLA", 2017, code="B2"),  # קוד דגם אחר = שורה אחרת
        raw("מאזדה", "3", 2019),
    ])
    rows = fleet_stats.sort_rows(counts.values())

    assert rows[0]["make"] == "טויוטה"
    assert rows[0]["vehicles"] == 2
    assert (rows[0]["year_from"], rows[0]["year_to"]) == (2015, 2019)
    assert len(rows) == 3


def test_aggregate_records_skips_empty_rows():
    counts = fleet_stats.aggregate_records([{}, raw("קיה", "פיקנטו", 2020)])
    assert len(counts) == 1


def test_sort_rows_applies_minimum():
    rows = fleet_stats.sort_rows(
        [{"make": "א", "model": "x", "vehicles": 3},
         {"make": "ב", "model": "y", "vehicles": 30}],
        min_count=10,
    )
    assert [row["model"] for row in rows] == ["y"]


def test_scan_counts_walks_pages_and_stops_on_total():
    pages = [[raw("טויוטה", "COROLLA", 2015)] * 2, [raw("מאזדה", "3", 2019)]]

    def fetch(offset, page_size):
        if offset == 0:
            return pages[0], 3
        if offset == 2:
            return pages[1], 3
        raise AssertionError("נמשך עמוד מיותר אחרי שהמאגר נגמר")

    rows = fleet_stats.scan_counts(page_size=2, fetch=fetch)
    assert [(row["model"], row["vehicles"]) for row in rows] == [("COROLLA", 2), ("3", 1)]


# ---- צילום המצב ----


def test_replace_snapshot_swaps_the_whole_table(app):
    snapshot(app, [{"make": "טויוטה", "model": "COROLLA", "model_code": "A1",
                    "vehicles": 90000, "year_from": 2010, "year_to": 2024}])
    saved = snapshot(app, [{"make": "מאזדה", "model": "3", "model_code": "B2",
                            "vehicles": 60000, "year_from": 2012, "year_to": 2023}])

    with app.app_context():
        rows = FleetModelCount.query.all()
        assert saved == 1
        assert [row.model for row in rows] == ["3"]  # הישן לא נשאר לצד החדש
        assert fleet_stats.summary()["vehicles"] == 60000
        assert rows[0].taken_at is not None


def test_search_and_totals(app):
    snapshot(app, [
        {"make": "טויוטה", "model": "COROLLA", "model_code": "A1", "vehicles": 90000},
        {"make": "טויוטה", "model": "YARIS", "model_code": "A2", "vehicles": 30000},
        {"make": "מאזדה", "model": "3", "model_code": "B2", "vehicles": 60000},
    ])
    with app.app_context():
        assert [r.model for r in fleet_stats.search().all()] == ["COROLLA", "3", "YARIS"]
        assert [r.model for r in fleet_stats.search(make="טויוטה").all()] == ["COROLLA", "YARIS"]
        assert [r.model for r in fleet_stats.search(q="oroll").all()] == ["COROLLA"]
        assert fleet_stats.total_vehicles(make="טויוטה") == 120000
        assert fleet_stats.makes() == ["טויוטה", "מאזדה"]
        assert fleet_stats.summary()["models"] == 3


def test_share_of_fleet(app):
    with app.app_context():
        row = FleetModelCount(make="טויוטה", model="COROLLA", vehicles=25)
        assert row.share(100) == 25.0
        assert row.share(0) == 0.0  # בלי סה"כ אין למה להשוות, ואין חלוקה באפס


def test_search_make_drops_country_of_origin(app):
    """הקישור מהמסך לחיפוש החלפים חייב לשאול על "טויוטה", לא "טויוטה יפן"."""
    with app.app_context():
        row = FleetModelCount(make="טויוטה יפן", model="COROLLA", vehicles=1)
        assert row.search_make == "טויוטה"


def test_stats_page_links_to_parts_search(client, app):
    snapshot(app, [{"make": "טויוטה יפן", "model": "COROLLA", "model_code": "A1",
                    "vehicles": 90000}])
    html = client.get("/stats").get_data(as_text=True)
    from urllib.parse import quote_plus

    assert f"/vehicles?make={quote_plus('טויוטה')}&amp;model=COROLLA" in html


def test_to_csv_has_bom_and_rows():
    output = fleet_stats.to_csv([{"make": "טויוטה", "model": "COROLLA",
                                  "model_code": None, "vehicles": 90000,
                                  "year_from": 2010, "year_to": 2024}])
    assert output.startswith("﻿")  # בלי זה אקסל פותח ג'יבריש
    assert "COROLLA,,90000,2010,2024" in output


# ---- המסך ----


def test_stats_page_lists_models(client, app):
    snapshot(app, [
        {"make": "טויוטה", "model": "COROLLA", "model_code": "A1", "vehicles": 90000},
        {"make": "מאזדה", "model": "3", "model_code": "B2", "vehicles": 60000},
    ])
    html = client.get("/stats").get_data(as_text=True)
    assert "90,000" in html
    assert "COROLLA" in html
    assert "150,000" in html  # סה"כ הצי


def test_stats_page_filters(client, app):
    snapshot(app, [
        {"make": "טויוטה", "model": "COROLLA", "model_code": "A1", "vehicles": 90000},
        {"make": "מאזדה", "model": "3", "model_code": "B2", "vehicles": 60000},
    ])
    html = client.get("/stats?make=מאזדה").get_data(as_text=True)
    assert "COROLLA" not in html
    assert "60,000" in html


def test_stats_page_explains_itself_when_empty(client, app):
    with app.app_context():
        FleetModelCount.query.delete()
        fleet_stats.db.session.commit()
    html = client.get("/stats").get_data(as_text=True)
    assert "vehicle_stats.py" in html  # מסך ריק שלא אומר מה לעשות הוא באג
    # הפקודה אנגלית בתוך דף עברי: בלי dir="ltr" הדפדפן שובר אותה באמצע
    # הדגלים, ו-"--scan --load" נקרא הפוך
    assert 'dir="ltr"><code>python scripts/vehicle_stats.py --scan --load' in html


def test_stats_csv_export(client, app):
    snapshot(app, [{"make": "טויוטה", "model": "COROLLA", "model_code": "A1",
                    "vehicles": 90000}])
    response = client.get("/stats.csv")
    body = response.get_data(as_text=True)
    assert response.headers["Content-Disposition"].endswith("fleet_by_model.csv")
    assert "COROLLA" in body
