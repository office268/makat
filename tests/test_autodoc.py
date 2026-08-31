"""גריד החלפים מ-Autodoc.

שתי שכבות נבדקות כאן, ובנפרד:

  1. הפירוק - HTML שנשמר כקובץ, בלי רשת ובלי Scrapy. זה החלק שיישבר
     כשהאתר ישנה את המבנה שלו, ולכן זה החלק שאפשר לתקן מול קובץ.
  2. העבודה - הצנרת שמכניסה את מה שנגרד לקטלוג, עם גריד מזויף. שום
     בדיקה כאן לא יוצאת לאינטרנט.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from app import autodoc
from app import parts_discovery as pd
from app.models import Part

FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scraper"))


def listing_html():
    return (FIXTURES / "autodoc_listing.html").read_text(encoding="utf-8")


def scraped(number="W 712/94", maker="MANN-FILTER", title="מסנן שמן", **extra):
    """שורה כפי שהגריד מחזיר אותה."""
    row = {"part_number": number, "manufacturer": maker, "title": title,
           "price": 24.9, "currency": "ILS", "oe_numbers": [],
           "url": "https://www.autodoc.co.il/p/1", "listing_url": "https://x/y"}
    row.update(extra)
    return row


# ---- פירוק העמוד ----


def test_json_ld_is_read_before_the_html():
    """כשהאתר פולט Product מובנה, הוא המקור - לא ניחוש סלקטורים."""
    parsers = pytest.importorskip("autodoc_scraper.parsers")
    rows = parsers.parse_listing(listing_html(), "https://www.autodoc.co.il/list")
    assert [row["part_number"] for row in rows] == ["W 712/94", "F 026 407 006"]
    assert rows[0]["manufacturer"] == "MANN-FILTER"
    assert rows[0]["price"] == 24.9
    assert all(row["source"] == "json-ld" for row in rows)


def test_a_relative_url_becomes_absolute():
    """הכתובת נשמרת כמקור של המק"ט, ולכן היא חייבת להיות לחיצה."""
    parsers = pytest.importorskip("autodoc_scraper.parsers")
    rows = parsers.parse_listing(listing_html(), "https://www.autodoc.co.il/list")
    assert rows[1]["url"] == "https://www.autodoc.co.il/bosch/5678"


def test_html_tiles_are_the_fallback():
    """בלי JSON-LD קוראים את האריחים, כולל מספר שכתוב בטקסט חופשי."""
    parsers = pytest.importorskip("autodoc_scraper.parsers")
    html = listing_html()
    stripped = html[: html.index("<script")] + html[html.index("</head>"):]
    rows = parsers.parse_listing(stripped, "https://www.autodoc.co.il/list")
    numbers = [row["part_number"] for row in rows]
    assert numbers == ["W 712/94", "B2F-300201"]
    assert rows[0]["price"] == 24.9
    assert all(row["source"] == "html" for row in rows)


def test_european_and_israeli_prices_both_parse():
    parsers = pytest.importorskip("autodoc_scraper.parsers")
    assert parsers._number("₪ 1,299.90") == 1299.9
    assert parsers._number("1.299,90 €") == 1299.9
    assert parsers._number("אין מחיר") is None


def test_oe_numbers_come_from_the_product_page():
    parsers = pytest.importorskip("autodoc_scraper.parsers")
    html = (FIXTURES / "autodoc_product.html").read_text(encoding="utf-8")
    assert parsers.parse_oe_numbers(html) == ["04152-YZZA1", "90915-YZZD2"]


# ---- בניית הכתובת ----


def test_the_url_is_built_from_the_hebrew_make():
    targets = pytest.importorskip("autodoc_scraper.targets")
    url = targets.listing_url("טויוטה", "COROLLA", "oil_filter")
    assert url.endswith("/car-parts/oil-filter/toyota/corolla")


def test_a_part_type_without_a_mapping_gets_no_url():
    """לא מנחשים כתובת: מטרה בלי מיפוי נרשמת ביומן במקום להחזיר 404."""
    targets = pytest.importorskip("autodoc_scraper.targets")
    assert targets.listing_url("טויוטה", "COROLLA", "third_brake_light") is None
    assert targets.listing_url("טויוטה", "", "oil_filter") is None


def test_the_mapping_can_be_fixed_from_the_environment(monkeypatch):
    """מבנה כתובות שהשתנה מתוקן בלי פריסה מחדש."""
    targets = pytest.importorskip("autodoc_scraper.targets")
    monkeypatch.setenv("AUTODOC_CATEGORIES", json.dumps({"oil_filter": "olfilter-10360"}))
    assert targets.category_of("oil_filter") == "olfilter-10360"
    monkeypatch.setenv("AUTODOC_CATEGORIES", "לא JSON")
    assert targets.category_of("oil_filter") == "oil-filter"


# ---- מהשורה הגרודה למועמד ----


def test_the_title_is_what_the_marque_guard_reads():
    """המקרה שנתפס בפועל: חלף של יצרן אחר בעמוד של הדגם שביקשנו."""
    rows = [scraped(number="F300201", maker="JC PREMIUM",
                    title="מסנן שמן CHERY AMULET")]
    candidates = autodoc.to_candidates(rows, "טויוטה", "COROLLA", "oil_filter")
    ok, bad = pd.validate(candidates, "טויוטה", "COROLLA", "oil_filter")
    assert ok == []
    assert "chery" in bad[0][1].lower()


def test_a_clean_row_becomes_a_candidate():
    candidates = autodoc.to_candidates([scraped(oe_numbers=["90915-YZZD2"])],
                                       "טויוטה", "COROLLA", "oil_filter")
    assert candidates[0]["oe_number"] == "90915-YZZD2"
    assert candidates[0]["source_url"] == "https://www.autodoc.co.il/p/1"
    ok, _ = pd.validate(candidates, "טויוטה", "COROLLA", "oil_filter")
    assert len(ok) == 1


def test_rows_that_are_not_objects_are_skipped():
    assert autodoc.to_candidates(["רעש", None], "טויוטה", "COROLLA", "oil_filter") == []


# ---- הפעלת תת-התהליך ----


def test_a_failed_run_reports_what_scrapy_said(app, monkeypatch):
    """קוד יציאה בלי הודעה אינו ניתן לאבחון, ולכן ה-stderr נכנס להודעה."""
    monkeypatch.setattr(autodoc, "available", lambda: True)

    def failing(*_args, **_kwargs):
        return subprocess.CompletedProcess([], 1, "", "ImportError: אין parsel")

    monkeypatch.setattr(subprocess, "run", failing)
    with app.app_context():
        with pytest.raises(RuntimeError, match="parsel"):
            autodoc.run_spider("טויוטה", "COROLLA", "oil_filter")


def test_a_run_that_hangs_is_killed(app, monkeypatch):
    """הרצה תקועה נהרגת לפני שה-timeout של gunicorn הורג את הבקשה."""
    monkeypatch.setattr(autodoc, "available", lambda: True)

    def hanging(*_args, **kwargs):
        raise subprocess.TimeoutExpired("scrapy", kwargs.get("timeout", 0))

    monkeypatch.setattr(subprocess, "run", hanging)
    with app.app_context():
        with pytest.raises(RuntimeError, match="לא סיים"):
            autodoc.run_spider("טויוטה", "COROLLA", "oil_filter")


def test_without_scrapy_the_error_says_so(app, monkeypatch):
    monkeypatch.setattr(autodoc, "available", lambda: False)
    with app.app_context():
        with pytest.raises(RuntimeError, match="Scrapy"):
            autodoc.run_spider("טויוטה", "COROLLA", "oil_filter")


def test_a_corrupt_output_file_is_no_rows(tmp_path):
    broken = tmp_path / "parts.json"
    broken.write_text("{לא JSON", encoding="utf-8")
    assert autodoc._read_rows(broken) == []
    assert autodoc._read_rows(tmp_path / "missing.json") == []


# ---- העבודה ----


def _run_one(app, rows, make="טויוטה", model="COROLLA", part_type="oil_filter"):
    """מריץ מטרה אחת עם גריד מזויף, ומחזיר את העבודה."""
    job = autodoc.start_job([[make, model, part_type]])
    return autodoc.run_step(
        job, spider=lambda mk, md, pt: autodoc.to_candidates(rows, mk, md, pt)
    )


def test_a_scraped_part_enters_the_catalog_marked_as_such(app):
    with app.app_context():
        job = _run_one(app, [scraped(number="SCR-1")])
        assert (job.created, job.rejected) == (1, 0)
        assert job.status == pd.DiscoveryJob.DONE
        part = Part.query.filter_by(part_number="SCR-1").one()
        assert pd.AUTODOC_MARK in part.notes
        assert pd.part_source_label(part) == "גריד Autodoc"
        # ההתאמה היא מה שמאפשר למצוא את החלף בחיפוש לפי מספר רישוי
        assert [(f.make, f.model) for f in part.fitments] == [("טויוטה", "COROLLA")]


def test_a_rejected_row_is_counted_and_logged(app):
    with app.app_context():
        job = _run_one(app, [scraped(number="SCR-2", title="מסנן BMW E90")])
        assert (job.created, job.rejected) == (0, 1)
        assert "bmw" in job.log.lower()
        assert Part.query.filter_by(part_number="SCR-2").first() is None


def test_a_failing_target_does_not_stop_the_run(app):
    """כשל בדגם אחד לא מפיל את ההרצה - הוא נרשם והמטרה הבאה רצה."""
    with app.app_context():
        job = autodoc.start_job([["טויוטה", "COROLLA", "oil_filter"],
                                 ["טויוטה", "COROLLA", "air_filter"]])

        def falling(*_args):
            raise RuntimeError("האתר החזיר 403")

        job = autodoc.run_step(job, spider=falling)
        assert job.cursor == 1 and job.is_running
        assert "403" in job.error

        job = autodoc.run_step(
            job, spider=lambda mk, md, pt: autodoc.to_candidates(
                [scraped(number="SCR-3")], mk, md, pt)
        )
        assert job.status == pd.DiscoveryJob.DONE
        assert job.created == 1


def test_the_two_sources_do_not_block_each_other(app):
    """גריד שרץ אינו חוסם חיפוש דרך המודל: שני מסכים, שתי הרצות."""
    with app.app_context():
        grid = autodoc.start_job([["טויוטה", "COROLLA", "oil_filter"]])
        claude = pd.start_job([["טויוטה", "COROLLA", "oil_filter"]], source=pd.CLAUDE)
        assert grid.id != claude.id
        assert autodoc.active_job().id == grid.id
        assert pd.active_job(pd.CLAUDE).id == claude.id
        assert autodoc.latest_job().source == pd.AUTODOC


# ---- המסך ----


def _login_superadmin(app, client):
    app.config["SUPERADMIN_EMAILS"] = frozenset({"fixture@t.test"})
    client.post("/login", data={"phone": "0500000001"})


def test_the_screen_needs_a_superadmin(auth_client):
    assert auth_client.get("/admin/autodoc").status_code == 403
    for path in ("/admin/autodoc/start", "/admin/autodoc/step",
                 "/admin/autodoc/cancel"):
        assert auth_client.post(path).status_code == 403


def test_the_screen_says_when_scrapy_is_missing(app, client, monkeypatch):
    monkeypatch.setattr(autodoc, "available", lambda: False)
    _login_superadmin(app, client)
    html = client.get("/admin/autodoc").get_data(as_text=True)
    assert "Scrapy אינו מותקן בשרת" in html
    response = client.post("/admin/autodoc/start",
                           data={"make": "טויוטה", "model": "COROLLA",
                                 "part_type": "oil_filter"})
    assert response.status_code == 400
    assert "Scrapy" in response.get_json()["error"]


def test_start_then_step_walks_the_targets(app, client, monkeypatch):
    monkeypatch.setattr(autodoc, "available", lambda: True)
    monkeypatch.setattr(
        autodoc, "searcher",
        lambda mk, md, pt: autodoc.to_candidates([scraped(number="WEB-1")], mk, md, pt),
    )
    _login_superadmin(app, client)

    started = client.post("/admin/autodoc/start",
                          data={"make": "טויוטה", "model": "COROLLA",
                                "part_type": "oil_filter"}).get_json()
    assert started["job"]["total"] == 1
    assert started["job"]["source"] == pd.AUTODOC

    stepped = client.post("/admin/autodoc/step").get_json()
    assert stepped["job"]["created"] == 1
    assert stepped["job"]["is_running"] is False
    # אין הרצה פעילה יותר, ובקשה נוספת אומרת את זה במקום להיתקע
    assert client.post("/admin/autodoc/step").status_code == 409


def test_the_plan_says_what_will_run(app, client):
    _login_superadmin(app, client)
    plan = client.get("/admin/autodoc/plan",
                      query_string={"make": "טויוטה", "model": "COROLLA",
                                    "part_type": "oil_filter"}).get_json()
    assert plan["count"] == 1
    assert plan["sample"] == ["טויוטה COROLLA · מסנן שמן"]


def test_cancelling_stops_the_run(app, client):
    _login_superadmin(app, client)
    with app.app_context():
        autodoc.start_job([["טויוטה", "COROLLA", "oil_filter"]])
    payload = client.post("/admin/autodoc/cancel").get_json()
    assert payload["job"]["status"] == pd.DiscoveryJob.CANCELLED
