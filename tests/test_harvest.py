"""קציר מק"טים לפי סבבים.

הקציר עולה כסף - בקשת רשת וקריאת מודל לכל שליפה - ולכן שני המספרים
שהמשתמש נותן הם התקציב כולו. הבדיקות כאן שומרות בדיוק על זה: שהסבב
נעצר כשהגיע ליעד, שסבב שני לא חוזר על אותם עמודים, ושמה שהסקיל מדד
כלא-קיים לא נשלף בכלל.
"""
import json

import pytest

from app import harvest
from app.auth_models import Organization, User
from app.models import Part, db
from app.taxonomy import PART_TYPES

SUPERADMIN = "harvest@t.test"
SUPERADMIN_PHONE = "0507770099"


@pytest.fixture
def admin_client(app, client):
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


def rows(count, prefix="OIL", brand="טויוטה", model="COROLLA", name="Oil Filter"):
    """שורות כמו שהמודל מחזיר אותן מעמוד קטגוריה."""
    return [
        {
            "part_number": f"{prefix}-{index:05d}",
            "brand": brand,
            "model": model,
            "year_from": 2015,
            "year_to": 2020,
            "name": name,
        }
        for index in range(count)
    ]


def reader_for(pages):
    """קורא-עמודים מזויף: מיפוי (אתר, תחילית, עמוד) -> שורות."""
    calls = []

    def read(site_key, prefix, page):
        calls.append((site_key, prefix, page))
        return pages.get((site_key, prefix, page), []), harvest.build_url(
            site_key, prefix, page
        )

    read.calls = calls
    return read


def drain(job, reader, guard=60):
    steps = 0
    while job.is_running and steps < guard:
        harvest.run_step(job, reader=reader)
        steps += 1
    return job


# --------------------------------------------------------------------------
# מה שהסקיל מדד, כקוד
# --------------------------------------------------------------------------

def test_the_url_matches_what_the_skill_measured():
    assert harvest.build_url("toyota", "oil_filter") == (
        "https://toyotapartsdeal.com/oem-toyota-oil_filter.html"
    )
    # מאזדה היא היחידה בלי הסיומת. נמדד, ולכן מקובע.
    assert harvest.build_url("mazda", "oil_filter") == (
        "https://mazdapartsnow.com/oem-mazda-oil_filter"
    )
    # אצל פולקסווגן היצרן בכתובת הוא השם המלא ולא ה-slug של האתר
    assert harvest.build_url("volkswagen", "air_filter").startswith(
        "https://vwpartsgiant.com/oem-volkswagen-"
    )
    assert harvest.build_url("toyota", "oil_filter", 2).endswith("?page=2")


def test_measured_404s_are_never_planned(app):
    """גישוש חוזר במה שכבר נמדד כלא קיים הוא שליפה מבוזבזת."""
    with app.app_context():
        planned = {
            (site, prefix) for site, prefix, _page in harvest.plan_round(10_000)
        }
    for pair in harvest.KNOWN_404:
        assert pair not in planned, pair
    # thermostat אצל ג'י.אם מחזיר עמוד ריק ולא 404 - אותה תוצאה
    assert ("gm", "thermostat") not in planned


def test_every_prefix_maps_to_a_real_part_type():
    for prefix in harvest.PREFIX_TYPES:
        assert harvest.PREFIX_TYPES[prefix] in PART_TYPES, prefix
    for prefix, (front, rear) in harvest.SIDED_PREFIXES.items():
        assert front in PART_TYPES and rear in PART_TYPES, prefix


def test_planning_starts_from_the_biggest_gap(app):
    """סוג חלק בתולי הוא הווקטור החזק - הוא פותח את כל היצרנים בבת אחת."""
    with app.app_context():
        counts = {"oil_filter": 900, "air_filter": 800}
        ranked = harvest.ranked_prefixes(counts)
        assert ranked[-1] in ("oil_filter", "air_filter")
        assert harvest.PREFIX_TYPES.get(ranked[0], "") not in ("oil_filter",
                                                               "air_filter")


# --------------------------------------------------------------------------
# הצד לא מנוחש
# --------------------------------------------------------------------------

def test_the_side_comes_from_the_name_only():
    assert harvest.resolve_type("brake_disc", "Front Brake Disc") == "brake_disc_front"
    assert harvest.resolve_type("brake_disc", "Rear Disc, RR.") == "brake_disc_rear"


def test_a_name_that_says_both_sides_yields_no_type():
    """שם שנוקב בשניהם אינו מוסר צד - הוא מוסר שניים."""
    assert harvest.resolve_type("brake_pad_set", "Disc Brake Pad Set, Front Rear") is None
    assert harvest.resolve_type("brake_pad_set", "Disc Brake Pad Set") is None


def test_rows_without_model_or_side_are_skipped():
    raw = [
        {"part_number": "A-1", "brand": "טויוטה", "model": "", "name": "Front Disc"},
        {"part_number": "", "brand": "טויוטה", "model": "COROLLA", "name": "Front Disc"},
        {"part_number": "C-3", "brand": "טויוטה", "model": "YARIS", "name": "Brake Disc"},
        {"part_number": "D-4", "brand": "טויוטה", "model": "YARIS", "name": "Front Disc"},
    ]
    kept, skipped = harvest.to_rows(raw, "brake_disc", "https://x.test/")
    assert [row["part_number"] for row in kept] == ["D-4"]
    assert len(skipped) == 3


# --------------------------------------------------------------------------
# הסבב
# --------------------------------------------------------------------------

def test_a_round_stops_at_the_number_the_user_asked_for(app):
    with app.app_context():
        job = harvest.start_job(wanted_per_round=5, rounds=1)
        targets = job.target_list
        pages = {tuple(target): rows(4, prefix=f"P{index}")
                 for index, target in enumerate(targets[:5])}
        reader = reader_for(pages)
        drain(job, reader)

        assert job.status == harvest.HarvestJob.DONE
        assert job.added_total >= 5
        # שתי שליפות מספיקות ל-8 מק"טים; השלישית לא נדרשה
        assert len(reader.calls) == 2


def test_a_round_that_runs_out_of_targets_ends_without_reaching_the_number(app):
    with app.app_context():
        job = harvest.start_job(wanted_per_round=400, rounds=1)
        drain(job, reader_for({}))
        assert job.status == harvest.HarvestJob.DONE
        assert job.added_total == 0
        assert job.fetches == len(job.target_list)


def test_the_second_round_does_not_re_fetch_the_same_pages(app):
    with app.app_context():
        job = harvest.start_job(wanted_per_round=3, rounds=2)
        reader = reader_for({})
        drain(job, reader)
        assert job.status == harvest.HarvestJob.DONE
        assert len(reader.calls) == len(set(reader.calls)), "אותו עמוד נשלף פעמיים"


def test_a_page_that_repeats_the_previous_one_stops_that_category(app):
    """אאודי ופולקסווגן החזירו ב-?page=2 בדיוק את עמוד 1, בלי שום סימן.

    זה הכשל היקר: אין 404, אין שדה ריק, ובלי לזהות אותו משלמים על עמוד
    3 של אותה קטגוריה עוד שליפה שכבר ידוע מה תחזיר.
    """
    with app.app_context():
        job = harvest.start_job(wanted_per_round=400, rounds=1)
        site_key, prefix, _page = job.target_list[0]
        # שלושת העמודים של אותה קטגוריה, כי בתכנון רגיל עמוד 2 מגיע
        # הרבה אחרי תקרת השליפות ולא היה נשלף בכלל
        job.targets = json.dumps([[site_key, prefix, page]
                                  for page in range(1, harvest.MAX_PAGE + 1)])
        db.session.commit()

        same = rows(3, prefix="DUP")
        reader = reader_for({(site_key, prefix, page): same
                             for page in range(1, harvest.MAX_PAGE + 1)})
        drain(job, reader)

        # עמוד 1 נכתב, עמוד 2 זוהה כחזרה, ועמוד 3 לא נשלף כלל
        assert job.added_total == 3
        assert "חוזר על העמוד הקודם" in job.log
        pages = [page for site, pfx, page in reader.calls
                 if (site, pfx) == (site_key, prefix)]
        assert pages == [1, 2]


def test_a_failing_fetch_does_not_kill_the_harvest(app):
    with app.app_context():
        job = harvest.start_job(wanted_per_round=400, rounds=1)
        first = tuple(job.target_list[0])

        def reader(site_key, prefix, page):
            if (site_key, prefix, page) == first:
                raise RuntimeError("האתר לא נגיש")
            return rows(2), harvest.build_url(site_key, prefix, page)

        harvest.run_step(job, reader=reader)
        assert job.is_running
        assert "האתר לא נגיש" in job.error
        harvest.run_step(job, reader=reader)
        assert job.added_total > 0
        assert job.error is None


def test_what_is_harvested_lands_in_the_catalog_with_its_own_mark(app):
    with app.app_context():
        job = harvest.start_job(wanted_per_round=2, rounds=1)
        target = tuple(job.target_list[0])
        reader = reader_for({target: rows(2, prefix="HARV")})
        drain(job, reader)

        part = Part.query.filter_by(part_number="HARV-00000").first()
        assert part is not None
        assert harvest.SOURCE_MARK in part.notes
        fitment = part.fitments[0]
        # טווח השנים של העמוד נשמר כטווח, לא כשנה אחת
        assert (fitment.year_from, fitment.year_to) == (2015, 2020)


def test_two_jobs_cannot_run_at_once(app):
    with app.app_context():
        harvest.start_job(wanted_per_round=5, rounds=1)
        with pytest.raises(ValueError, match="כבר רץ"):
            harvest.start_job(wanted_per_round=5, rounds=1)


@pytest.mark.parametrize("wanted,rounds,message", [
    (0, 1, "לפחות"),
    (1, 0, "לפחות"),
    (10_000, 1, "בסבב"),
    (1, 999, "סבבים"),
    ("שלוש", 1, "מספרים"),
])
def test_unreasonable_numbers_are_refused(app, wanted, rounds, message):
    with app.app_context():
        with pytest.raises(ValueError, match=message):
            harvest.start_job(wanted_per_round=wanted, rounds=rounds)


# --------------------------------------------------------------------------
# המסך
# --------------------------------------------------------------------------

def test_the_screen_asks_for_both_numbers(admin_client):
    body = admin_client.get("/admin/harvest").get_data(as_text=True)
    assert 'id="wanted"' in body and 'id="rounds"' in body
    assert "כמה מק\"טים חדשים בכל סבב" in body
    assert "כמה סבבים" in body


def test_the_plan_says_what_it_will_cost_before_starting(admin_client):
    payload = admin_client.get("/admin/harvest/plan?wanted=90&rounds=3").get_json()
    assert payload["fetches"] >= 1
    assert payload["total_fetches"] == payload["fetches"] * 3
    assert payload["sample"]


def test_starting_without_numbers_is_refused(admin_client, monkeypatch):
    monkeypatch.setattr(harvest, "available", lambda: True)
    response = admin_client.post("/admin/harvest/start", data={"wanted": "0",
                                                               "rounds": "1"})
    assert response.status_code == 400
    assert "לפחות" in response.get_json()["error"]


def test_the_screen_is_closed_to_a_normal_user(client):
    assert client.get("/admin/harvest").status_code == 403


def test_read_only_blocks_the_harvest(admin_client, app, monkeypatch):
    """הקציר כותב לקטלוג, ולכן הנעילה חלה עליו כמו על כל עריכה."""
    monkeypatch.setattr(harvest, "available", lambda: True)
    app.config["READ_ONLY"] = True
    try:
        response = admin_client.post(
            "/admin/harvest/start", data={"wanted": "5", "rounds": "1"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert response.status_code == 403
    finally:
        app.config["READ_ONLY"] = False
