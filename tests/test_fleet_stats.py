"""ספירת הרכבים הפעילים לפי דגם: משיכה, צבירה, צילום מצב והמסך."""
import json
from datetime import datetime
from contextlib import contextmanager

from app import fleet_stats, services
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
                                  "young": 10000, "prime": 60000, "old": 20000,
                                  "year_from": 2010, "year_to": 2024}])
    assert output.startswith("﻿")  # בלי זה אקסל פותח ג'יבריש
    assert "בטווח הקנייה" in output
    assert "COROLLA,,90000,10000,60000,20000,2010,2024" in output


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


def test_stats_page_counts_matching_parts(client, app):
    """הקטלוג של הבדיקות מחזיק רפידות קדמיות ל-COROLLA של טויוטה."""
    snapshot(app, [
        {"make": "טויוטה יפן", "model": "COROLLA", "model_code": "A1",
         "vehicles": 90000},
        {"make": "מאזדה יפן", "model": "3", "model_code": "B2", "vehicles": 60000},
    ])
    html = client.get("/stats?make=טויוטה יפן").get_data(as_text=True)
    assert "חלפים במאגר" in html
    assert "מתוכם מתכלים" in html
    # שורת COROLLA: מק"ט אחד מתאים, והוא מתכלה
    row = html[html.index("COROLLA"):]
    assert "badge bg-success-subtle" in row


def test_part_counts_match_what_the_search_opens(app):
    """המספר בעמודה הוא בדיוק מה שנפתח בלחיצה עליו."""
    with app.app_context():
        total, wear = services.vehicle_part_counts("טויוטה", "COROLLA")
        assert total == services.search_parts(make="טויוטה", model="COROLLA").count()
        assert (total, wear) == (1, 1)


def test_part_counts_are_zero_for_a_model_we_carry_nothing_for(app):
    with app.app_context():
        assert services.vehicle_part_counts("מאזדה", "3") == (0, 0)


def test_wear_count_excludes_parts_that_are_not_consumables(app):
    """פגוש מתאים לרכב אבל אינו מתכלה - הוא נספר רק בעמודה הכללית."""
    from app.models import Fitment, Part, db

    with app.app_context():
        part = Part(part_number="BUMPER-1", name_he="פגוש קדמי COROLLA",
                    part_type="front_bumper")
        part.fitments = [Fitment(make="טויוטה", model="COROLLA")]
        db.session.add(part)
        db.session.commit()

        total, wear = services.vehicle_part_counts("טויוטה", "COROLLA")
        assert (total, wear) == (2, 1)


# ---- פילוח גיל ויחס הפער ----


def test_age_buckets_split_the_aftermarket_window(app):
    """4-12 שנים הוא חלון הקנייה: מחוץ לאחריות ועדיין על הכביש."""
    assert fleet_stats.age_bucket(2024, this_year=2026) == "young"
    assert fleet_stats.age_bucket(2022, this_year=2026) == "prime"   # בן 4
    assert fleet_stats.age_bucket(2014, this_year=2026) == "prime"   # בן 12
    assert fleet_stats.age_bucket(2013, this_year=2026) == "old"     # בן 13
    assert fleet_stats.age_bucket(None) is None


def test_aggregate_counts_each_age_group(app, monkeypatch):
    monkeypatch.setattr(fleet_stats, "_now", lambda: datetime(2026, 1, 1))
    rows = [{fleet_stats.FIELD_MAKE: "טויוטה יפן",
             fleet_stats.FIELD_MODEL: "COROLLA",
             fleet_stats.FIELD_CODE: "A1",
             fleet_stats.FIELD_YEAR: year}
            for year in (2025, 2020, 2018, 2005, None)]
    row = fleet_stats.sort_rows(fleet_stats.aggregate_records(rows).values())[0]

    assert row["vehicles"] == 5
    assert (row["young"], row["prime"], row["old"]) == (1, 2, 1)
    # רכב בלי שנת ייצור אינו בשום דלי, ולכן הסכום קטן מסך הרכבים
    assert row["young"] + row["prime"] + row["old"] == 4


def test_packed_counts_keep_the_age_split():
    counts = {("טויוטה", "COROLLA", "A1"): {
        "make": "טויוטה", "model": "COROLLA", "model_code": "A1",
        "vehicles": 9, "year_from": 2010, "year_to": 2024,
        "young": 2, "prime": 5, "old": 2}}
    restored = fleet_stats.unpack_counts(fleet_stats.pack_counts(counts))
    assert restored == counts


def test_vehicles_per_part_has_no_denominator_without_parts(app):
    with app.app_context():
        row = FleetModelCount(make="טויוטה", model="COROLLA", vehicles=100, prime=80)
        assert row.vehicles_per_part(4) == 20
        # אין מק"טים -> אין יחס, וזה לא "אפס" אלא הפער המרבי
        assert row.vehicles_per_part(0) is None


def test_prime_sort_puts_the_buying_window_first(app):
    snapshot(app, [
        {"make": "טויוטה יפן", "model": "COROLLA", "vehicles": 90000, "prime": 20000},
        {"make": "סקודה צכיה", "model": "OCTAVIA", "vehicles": 60000, "prime": 50000},
    ])
    with app.app_context():
        by_fleet = [r.model for r in fleet_stats.search().all()]
        by_prime = [r.model for r in fleet_stats.search(sort="prime").all()]
    assert by_fleet == ["COROLLA", "OCTAVIA"]
    assert by_prime == ["OCTAVIA", "COROLLA"]


def test_gap_view_ranks_by_vehicles_per_part(client, app):
    """COROLLA גדול יותר, אבל יש לנו מק"ט אחד עבורו; OCTAVIA - אף אחד."""
    snapshot(app, [
        {"make": "טויוטה יפן", "model": "COROLLA", "vehicles": 90000, "prime": 50000},
        {"make": "סקודה צכיה", "model": "OCTAVIA", "vehicles": 60000, "prime": 40000},
    ])
    html = client.get("/stats?sort=gap").get_data(as_text=True)
    assert html.index("OCTAVIA") < html.index("COROLLA")
    assert "אין כיסוי" in html  # לדגם בלי מק"טים אין יחס להציג


def test_gap_view_merges_model_codes_and_maker_spellings(client, app):
    """אותו דגם בשני קודי דגם ובשני כתיבי יצרן הוא שוק אחד, לא שניים."""
    snapshot(app, [
        {"make": "מזדה יפן", "model": "MAZDA 2", "model_code": "DJ1",
         "vehicles": 20000, "prime": 12000},
        {"make": "מזדה תאילנד", "model": "MAZDA 2", "model_code": "DJ5",
         "vehicles": 12000, "prime": 10000},
        {"make": "טויוטה יפן", "model": "COROLLA", "model_code": "ZRE",
         "vehicles": 90000, "prime": 50000},
    ])
    with app.app_context():
        rows = fleet_stats.grouped_by_model()
        merged = {(row.make, row.model): (row.vehicles, row.prime) for row in rows}
        assert merged[("מזדה", "MAZDA 2")] == (32000, 22000)
        assert len(rows) == 2

    html = client.get("/stats?sort=gap").get_data(as_text=True)
    assert html.count("MAZDA 2") == 1  # שורה אחת, לא שתיים זהות


def test_registry_name_longer_than_the_catalog_still_matches(app):
    """הבאג שנצפה בייצור: COROLLA HSD SDN לא מצא את 265 המק"טים של COROLLA."""
    from app.models import Fitment, Part, db

    with app.app_context():
        part = Part(part_number="COR-1", name_he="מסנן שמן COROLLA",
                    part_type="oil_filter")
        part.fitments = [Fitment(make="טויוטה", model="COROLLA")]
        db.session.add(part)
        db.session.commit()

        # שלושת השמות שבמרשם מוצאים בדיוק את אותם מק"טים
        counts = {
            model: services.part_counts_for([("טויוטה", model)])[("טויוטה", model)]
            for model in ("COROLLA", "COROLLA HSD SDN", "COROLLA CROSS")
        }
        assert counts["COROLLA"][0] >= 1
        assert len(set(counts.values())) == 1, counts

        # ושם קצר לא נדבק לכל דגם שמכיל את אותן אותיות
        assert services.model_matches_name("I35", "I3") is False
        assert services.model_matches_name("MAZDA 3", "3") is False


def test_maker_spelling_is_bridged(app):
    """המרשם כותב "מזדה", הקטלוג "מאזדה" - אותו יצרן."""
    from app.models import Fitment, Part, db

    with app.app_context():
        part = Part(part_number="MZ-1", name_he="מסנן שמן", part_type="oil_filter")
        part.fitments = [Fitment(make="מאזדה", model="MAZDA 2")]
        db.session.add(part)
        db.session.commit()

        total, _ = services.part_counts_for(
            [("מזדה", "MAZDA 2")])[("מזדה", "MAZDA 2")]
        assert total == 1
        assert services.search_parts(make="מזדה", model="MAZDA 2").count() == 1

        # הנורמליזציה זהירה: היא לא ממזגת יצרנים שונים
        assert services.normalize_make("מאזדה") == services.normalize_make("מזדה")
        assert services.normalize_make("קיה") != services.normalize_make("סקודה")


def test_batch_counts_match_the_single_lookup(app):
    with app.app_context():
        pairs = [("טויוטה", "COROLLA"), ("מאזדה", "3")]
        batch = services.part_counts_for(pairs)
        for pair in pairs:
            assert batch[pair] == services.vehicle_part_counts(*pair)
            assert batch[pair][0] == services.search_parts(
                make=pair[0], model=pair[1]
            ).count()


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
