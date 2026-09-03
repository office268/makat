"""זריעת הקטלוג: מק"טים שמובאים מראש, מטרה אחת בכל פעם.

השליפה החיה לוקחת דקות בפעם הראשונה לכל דגם. זה בסדר לחלק נדיר, ולא
בסדר לרפידות של קורולה. המסך הזה מביא את הנפוצים מראש - ובקצב שבו
האדם שמשלם רואה כל מק"ט לפני שהוא משלם על הבא.
"""
import json

import pytest

from app import live_lookup, seed_catalog, vehicles
from app.catalog_sources import Candidate
from app.auth_models import Organization, User
from app.fleet_stats import FleetModelCount
from app.models import Part, db
from app.seed_catalog import SeedJob

SUPERADMIN = "root@makat.test"
SUPERADMIN_PHONE = "0506660001"


@pytest.fixture
def superadmin(app, client):
    app.config["SUPERADMIN_EMAILS"] = frozenset({SUPERADMIN})
    with app.app_context():
        organization = Organization.query.filter_by(slug="fixture-org").first()
        db.session.add(User(phone=SUPERADMIN_PHONE, email=SUPERADMIN,
                            role="owner", organization=organization))
        db.session.commit()
    client.post("/logout")
    client.post("/login", data={"phone": SUPERADMIN_PHONE})
    return client


COROLLA = {"plate": "1234567", "vin": "JTNBV58E20J147563", "make": "טויוטה יפן",
           "model": "COROLLA", "year": 2011, "engine_code": "1ZR",
           "model_code": "ZRE151L", "source": "data.gov.il"}


def _counts(app, rows):
    with app.app_context():
        for make, model, vehicles_n, prime in rows:
            db.session.add(FleetModelCount(make=make, model=model,
                                           vehicles=vehicles_n, prime=prime))
        db.session.commit()


# --------------------------------------------------------------------------
# מי נבחר
# --------------------------------------------------------------------------

def test_the_ranking_prefers_the_aftermarket_window_over_raw_volume(app):
    """‏prime הוא בני 4-12 - לא חדשים ולא ישנים מדי. דגם עם המון רכבים
    חדשים אינו פוטנציאל מכירה, וזו בדיוק ההבחנה."""
    _counts(app, [("טויוטה יפן", "COROLLA", 50_000, 40_000),
                  ("טסלה ארה\"ב", "MODEL 3", 90_000, 1_000)])
    with app.app_context():
        assert [row.model for row in seed_catalog.ranked_models(2)] == \
            ["COROLLA", "MODEL 3"]


def test_without_an_age_split_it_falls_back_to_total_vehicles(app):
    """צילום ישן בלי פילוח גיל עדיף על מסך שאומר 'אין נתונים'."""
    _counts(app, [("א", "AAA", 10, 0), ("ב", "BBB", 90, 0)])
    with app.app_context():
        assert [row.model for row in seed_catalog.ranked_models(2)] == ["BBB", "AAA"]


def test_a_model_without_a_real_vin_is_skipped_and_named(app):
    """דגם בלי רכב מייצג במרשם אינו מטרה - חיפוש הקטלוג הוא לפי שלדה."""
    _counts(app, [("טויוטה יפן", "COROLLA", 9, 9), ("נדיר", "GHOST", 8, 8)])
    with app.app_context():
        targets, skipped = seed_catalog.propose(
            2, part_types=["oil_filter"],
            lookup=lambda make, model: COROLLA if model == "COROLLA" else None,
        )
        assert [t["model"] for t in targets] == ["COROLLA"]
        assert skipped == ["נדיר GHOST"]


def test_every_chosen_part_gets_a_target_for_every_vehicle(app):
    _counts(app, [("טויוטה יפן", "COROLLA", 9, 9)])
    with app.app_context():
        targets, _ = seed_catalog.propose(
            1, part_types=["oil_filter", "air_filter", "לא_קיים"],
            lookup=lambda make, model: COROLLA,
        )
        assert [t["part_type"] for t in targets] == ["oil_filter", "air_filter"]
        assert all(t["vin"] == COROLLA["vin"] for t in targets)
        assert targets[0]["part_type_name"] == "מסנן שמן"


def test_the_default_part_types_are_all_real_keys():
    from app.taxonomy import PART_TYPES

    assert len(seed_catalog.DEFAULT_PART_TYPES) == 10
    assert all(key in PART_TYPES for key in seed_catalog.DEFAULT_PART_TYPES)


# --------------------------------------------------------------------------
# הקצב: מטרה אחת בכל פעם
# --------------------------------------------------------------------------

def _job(app, targets=None):
    rows = targets or [
        dict(COROLLA, part_type="oil_filter"),
        dict(COROLLA, part_type="air_filter"),
    ]
    return seed_catalog.start_job(rows)


def test_a_target_already_in_the_catalog_costs_nothing(app, monkeypatch):
    """זריעה שמביאה מה שכבר יש היא בזבוז קרדיטים של מי שלחץ."""
    started = []
    monkeypatch.setattr(live_lookup, "start_job",
                        lambda *a, **k: started.append(1))
    with app.app_context():
        part = Part(part_number="SEED-1", name_he="מסנן", part_type="oil_filter")
        db.session.add(part)
        db.session.commit()
        monkeypatch.setattr(seed_catalog.services, "parts_for_vehicle",
                            lambda vehicle, part_type: [part])
        job = _job(app)
        seed_catalog.run_step(job)
        assert started == [], "לא הייתה אמורה להיפתח שליפה חיה"
        assert job.cursor == 1 and job.found == 1
        assert json.loads(job.last_result)["numbers"] == ["SEED-1"]


def test_it_stops_after_each_part_and_waits(app, monkeypatch):
    """הלב של הקצב שנבחר: מק"ט אחד, ואז המתנה ללחיצה."""
    with app.app_context():
        monkeypatch.setattr(seed_catalog.services, "parts_for_vehicle",
                            lambda vehicle, part_type: [])
        monkeypatch.setattr(live_lookup, "cached", lambda *a, **k: {
            "results": [{"part_number": "90915-YZZD4"}], "unverified": []})
        job = _job(app)
        seed_catalog.run_step(job)
        assert job.cursor == 1
        assert job.awaiting is True, "היה אמור לעצור ולחכות"
        payload = job.to_dict()
        assert payload["awaiting"] is True
        assert payload["last_result"]["numbers"] == ["90915-YZZD4"]
        # ורק לחיצה נוספת מקדמת
        seed_catalog.run_step(job)
        assert job.cursor == 2 and job.status == SeedJob.DONE


def test_a_cached_empty_answer_is_not_a_failure(app, monkeypatch):
    with app.app_context():
        monkeypatch.setattr(seed_catalog.services, "parts_for_vehicle",
                            lambda vehicle, part_type: [])
        monkeypatch.setattr(live_lookup, "cached",
                            lambda *a, **k: {"results": [], "unverified": []})
        job = _job(app)
        seed_catalog.run_step(job)
        assert job.missing == 1 and job.failed == 0


def test_the_target_stays_open_while_the_live_lookup_runs(app, monkeypatch):
    """מטרה אחת אינה בקשה אחת: השליפה מנווטת בקטלוג לאורך כמה בקשות,
    והמונה מתקדם רק כשהיא נסגרה."""
    with app.app_context():
        monkeypatch.setattr(seed_catalog.services, "parts_for_vehicle",
                            lambda vehicle, part_type: [])
        monkeypatch.setattr(live_lookup, "cached", lambda *a, **k: None)
        monkeypatch.setattr(live_lookup, "usable_sources",
                            lambda vehicle: [_FakeSource()])
        job = _job(app)

        seed_catalog.run_step(job)          # פותח שליפה, לא מריץ אותה
        assert job.child_id, "שליפה חיה הייתה אמורה להיפתח"
        assert job.cursor == 0, "המטרה עדיין פתוחה"

        seed_catalog.run_step(job)          # מריץ את המקור היחיד
        child = db.session.get(live_lookup.LookupJob, job.child_id or 0)
        assert child is None or not child.is_running

        seed_catalog.run_step(job)          # קוצר את התוצאה וסוגר
        assert job.cursor == 1 and job.child_id is None
        # ‏mock הוא מקור אמיתי ורשום שמחזיר מק"ט - כלומר השרשרת כולה
        # עבדה: מטרה -> שליפה חיה -> אימות -> תוצאה על המטרה.
        assert job.found == 1
        assert json.loads(job.last_result)["numbers"]


class _FakeSource:
    """מקור בשם ``mock`` - זה המפתח של המקור הרשום, וכך העבודה מריצה
    אותו באמת. ההזרקה כאן היא רק כדי ש-``usable_sources`` יחזיר אותו
    בלי לתלות את הבדיקה בהגדרות הסביבה."""

    key, name, tier, needs_vin = "mock", "מדומה", "oem", True
    supports_resume = False

    def available(self):
        return True

    def lookup(self, vehicle, part_type, oem_numbers=(), **kwargs):
        return []


def test_cancelling_also_cancels_the_lookup_underneath(app, monkeypatch):
    with app.app_context():
        monkeypatch.setattr(seed_catalog.services, "parts_for_vehicle",
                            lambda vehicle, part_type: [])
        monkeypatch.setattr(live_lookup, "cached", lambda *a, **k: None)
        monkeypatch.setattr(live_lookup, "usable_sources",
                            lambda vehicle: [_FakeSource()])
        job = _job(app)
        seed_catalog.run_step(job)
        child_id = job.child_id
        assert child_id
        seed_catalog.cancel_job(job)
        child = db.session.get(live_lookup.LookupJob, child_id)
        assert job.status == SeedJob.CANCELLED
        assert child.status == live_lookup.LookupJob.CANCELLED


def test_a_quota_refusal_is_recorded_and_does_not_wedge_the_run(app, monkeypatch):
    """המכסה היומית נגמרה - זו מטרה שנכשלה, לא זריעה תקועה."""
    with app.app_context():
        monkeypatch.setattr(seed_catalog.services, "parts_for_vehicle",
                            lambda vehicle, part_type: [])
        monkeypatch.setattr(live_lookup, "cached", lambda *a, **k: None)
        monkeypatch.setattr(live_lookup, "start_job", _refuse)
        job = _job(app)
        seed_catalog.run_step(job)
        assert job.failed == 1 and job.cursor == 1
        assert "תקרת השליפות" in json.loads(job.last_result)["detail"]


def _refuse(*args, **kwargs):
    raise ValueError("נוצלה תקרת השליפות החיות ליממה (50).")


def test_a_second_run_reuses_the_open_one(app):
    with app.app_context():
        first = _job(app)
        second = seed_catalog.start_job([dict(COROLLA, part_type="oil_filter")])
        assert second.id == first.id


def test_an_empty_target_list_is_refused(app):
    with app.app_context():
        with pytest.raises(ValueError):
            seed_catalog.start_job([])


# --------------------------------------------------------------------------
# המסך
# --------------------------------------------------------------------------

def test_the_screen_is_superadmin_only(client):
    """מה שכאן משפיע על כל הלקוחות ועולה כסף, ולכן לא נפתח למנהל מוסך."""
    assert client.get("/admin/seed").status_code in (302, 403)
    assert client.post("/admin/seed/start", data={"targets": "[]"}).status_code \
        in (302, 403)


def test_the_admin_screen_opens(superadmin):
    assert 'זריעת מק"טים'.encode() in superadmin.get("/admin/seed").data


def test_starting_without_real_targets_is_refused(superadmin, monkeypatch):
    monkeypatch.setattr(live_lookup, "available", lambda: True)
    for payload in ("[]", "לא JSON", json.dumps([{"part_type": "oil_filter"}])):
        response = superadmin.post("/admin/seed/start", data={"targets": payload})
        assert response.status_code == 400, payload


def test_a_proposal_does_not_start_anything(superadmin, app, monkeypatch):
    """ההצעה היא לצפייה ולעריכה. היא לא מוציאה בקשת רשת אחת."""
    _counts(app, [("טויוטה יפן", "COROLLA", 9, 9)])
    monkeypatch.setattr(vehicles, "by_model", lambda make, model: COROLLA)
    response = superadmin.get("/admin/seed/propose?vehicles=1&part_type=oil_filter")
    assert response.status_code == 200
    body = response.get_json()
    assert body["count"] == 1 and body["vehicles"] == 1
    with app.app_context():
        assert seed_catalog.active_job() is None


# --------------------------------------------------------------------------
# הצי הקבוע: הרכבים כתובים, ולא נגזרים מחדש בכל לחיצה
# --------------------------------------------------------------------------

MAZDA = {"plate": "7654321", "vin": "JMZBM14F601234567", "make": "מאזדה יפן",
         "model": "3", "year": 2016, "engine_code": "PE", "model_code": "BM",
         "source": "data.gov.il"}


def test_without_a_saved_fleet_the_list_moves_under_your_feet(app):
    """זו הבעיה שהטבלה פותרת, ולכן היא נבדקת לפני הפתרון.

    צילום צי חדש מחליף את הסדר, וייבוא מרשם חדש מחליף את הרכב
    המייצג - ואותה לחיצה מייצרת מחר רשימה אחרת.
    """
    _counts(app, [("טויוטה יפן", "COROLLA", 9, 9)])
    with app.app_context():
        first, _ = seed_catalog.propose(1, part_types=["oil_filter"],
                                        lookup=lambda make, model: COROLLA)
        # אותו דגם, רכב מייצג אחר מהמרשם
        other = dict(COROLLA, vin="JTNBV58E20J999999", plate="9999999")
        second, _ = seed_catalog.propose(1, part_types=["oil_filter"],
                                         lookup=lambda make, model: other)
        assert first[0]["vin"] != second[0]["vin"]


def test_a_saved_fleet_is_read_and_not_rebuilt(app):
    """אחרי שנקבע, המרשם והצילום כבר לא משנים את הרשימה."""
    _counts(app, [("טויוטה יפן", "COROLLA", 9, 9)])
    with app.app_context():
        seed_catalog.save_fleet([COROLLA])
        targets, skipped = seed_catalog.propose(
            10, part_types=["oil_filter"],
            # מרשם שמחזיר רכב אחר לגמרי - ולא אמור להישאל בכלל
            lookup=lambda make, model: MAZDA,
        )
        assert [t["vin"] for t in targets] == [COROLLA["vin"]]
        assert skipped == []


def test_the_saved_fleet_keeps_the_order_it_was_given(app):
    with app.app_context():
        seed_catalog.save_fleet([MAZDA, COROLLA])
        assert [row.vin for row in seed_catalog.fleet()] == \
            [MAZDA["vin"], COROLLA["vin"]]
        assert [row.position for row in seed_catalog.fleet()] == [0, 1]


def test_saving_replaces_the_fleet_and_does_not_merge_into_it(app):
    """מיזוג היה מותיר ברשימה רכב שנמחק בעריכה."""
    with app.app_context():
        seed_catalog.save_fleet([COROLLA, MAZDA])
        seed_catalog.save_fleet([MAZDA])
        assert [row.vin for row in seed_catalog.fleet()] == [MAZDA["vin"]]


def test_the_same_vin_twice_is_saved_once(app):
    """אותו רכב פעמיים הוא אותה זריעה פעמיים, על חשבון מי שלוחץ."""
    with app.app_context():
        seed_catalog.save_fleet([COROLLA, dict(COROLLA, plate="0000000")])
        assert len(seed_catalog.fleet()) == 1


def test_a_fleet_without_a_vin_is_refused(app):
    with app.app_context():
        with pytest.raises(ValueError):
            seed_catalog.save_fleet([{"make": "טויוטה", "model": "COROLLA"}])


def test_an_inactive_vehicle_stays_on_record_but_out_of_the_plan(app):
    """ביטול החלטה שמוחק את הראיה הוא ביטול שאי אפשר לחזור ממנו."""
    with app.app_context():
        seed_catalog.save_fleet([COROLLA, dict(MAZDA, active=False)])
        assert [row.vin for row in seed_catalog.fleet()] == [COROLLA["vin"]]
        assert len(seed_catalog.fleet(include_inactive=True)) == 2


def test_the_first_seed_run_fixes_the_fleet_by_itself(app):
    """מי ששותק מקבל יציבות: הזריעה הראשונה כותבת את הרכבים."""
    with app.app_context():
        assert not seed_catalog.fleet_is_set()
        seed_catalog.start_job([dict(COROLLA, part_type="oil_filter"),
                                dict(COROLLA, part_type="air_filter"),
                                dict(MAZDA, part_type="oil_filter")])
        assert [row.vin for row in seed_catalog.fleet()] == \
            [COROLLA["vin"], MAZDA["vin"]]


def test_a_second_run_does_not_overwrite_a_fleet_that_was_set(app):
    with app.app_context():
        seed_catalog.save_fleet([MAZDA])
        job = seed_catalog.start_job([dict(COROLLA, part_type="oil_filter")])
        seed_catalog.cancel_job(job)
        assert [row.vin for row in seed_catalog.fleet()] == [MAZDA["vin"]]


def test_releasing_the_fleet_brings_the_derivation_back(app):
    _counts(app, [("טויוטה יפן", "COROLLA", 9, 9)])
    with app.app_context():
        seed_catalog.save_fleet([MAZDA])
        assert seed_catalog.clear_fleet() == 1
        targets, _, fixed, _ = seed_catalog.propose_detailed(
            1, part_types=["oil_filter"], lookup=lambda make, model: COROLLA)
        assert [t["vin"] for t in targets] == [COROLLA["vin"]]
        assert fixed is False


# --------------------------------------------------------------------------
# המסך
# --------------------------------------------------------------------------

def test_the_screen_saves_the_fleet_and_says_it_is_fixed(app, superadmin):
    response = superadmin.post("/admin/seed/fleet", data={
        "vehicles": json.dumps([COROLLA, MAZDA])})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["count"] == 2
    assert payload["fixed"] is True
    with app.app_context():
        assert [row.vin for row in seed_catalog.fleet()] == \
            [COROLLA["vin"], MAZDA["vin"]]


def test_the_proposal_says_whether_the_list_is_fixed(app, superadmin):
    _counts(app, [("טויוטה יפן", "COROLLA", 9, 9)])
    with app.app_context():
        seed_catalog.save_fleet([COROLLA])
    payload = superadmin.get(
        "/admin/seed/propose?vehicles=1&part_type=oil_filter").get_json()
    assert payload["fixed"] is True
    assert payload["vehicles"] == 1


def test_the_screen_can_release_the_fleet(app, superadmin):
    with app.app_context():
        seed_catalog.save_fleet([COROLLA])
    payload = superadmin.post("/admin/seed/fleet/clear").get_json()
    assert payload["removed"] == 1
    assert payload["fixed"] is False
    with app.app_context():
        assert not seed_catalog.fleet_is_set()


def test_a_fleet_save_without_vehicles_is_a_clear_error(app, superadmin):
    response = superadmin.post("/admin/seed/fleet", data={"vehicles": "[]"})
    assert response.status_code == 400
    assert "רכבים" in response.get_json()["error"]


# --------------------------------------------------------------------------
# ההצעה מציעה חורים, ולא את מה שכבר יש
# --------------------------------------------------------------------------

def _catalog_part(app, number, part_type, make, model, year_from, year_to):
    from app.models import Fitment, Part, db

    with app.app_context():
        part = Part(part_number=number, name_he="חלק", part_type=part_type)
        part.fitments.append(Fitment(make=make, model=model,
                                     year_from=year_from, year_to=year_to))
        db.session.add(part)
        db.session.commit()


def test_a_pair_already_in_the_catalog_is_not_proposed_again(app):
    """‏run_step ידע לסגור אותו בלי רשת, ולכן זה לא עלה כסף - אבל זה
    עלה בתשומת לב: חצי מהרשימה הייתה רעש שצריך לגלול."""
    _counts(app, [("טויוטה יפן", "COROLLA", 9, 9)])
    _catalog_part(app, "90915-YZZD4", "oil_filter", "טויוטה", "COROLLA", 2005, 2015)
    with app.app_context():
        targets, _, _, covered = seed_catalog.propose_detailed(
            1, part_types=["oil_filter", "air_filter"],
            lookup=lambda make, model: COROLLA)
    assert [t["part_type"] for t in targets] == ["air_filter"]
    assert covered == 1


def test_asking_for_everything_still_returns_everything(app):
    """‏gaps_only=False נחוץ למי שרוצה לרענן מק"ט קיים ולא לסגור חור."""
    _counts(app, [("טויוטה יפן", "COROLLA", 9, 9)])
    _catalog_part(app, "90915-YZZD4", "oil_filter", "טויוטה", "COROLLA", 2005, 2015)
    with app.app_context():
        targets, _, _, covered = seed_catalog.propose_detailed(
            1, part_types=["oil_filter", "air_filter"],
            lookup=lambda make, model: COROLLA, gaps_only=False)
    assert len(targets) == 2
    assert covered == 0


def test_the_screen_reports_how_many_pairs_were_already_covered(app, superadmin):
    _counts(app, [("טויוטה יפן", "COROLLA", 9, 9)])
    _catalog_part(app, "90915-YZZD4", "oil_filter", "טויוטה", "COROLLA", 2005, 2015)
    with app.app_context():
        seed_catalog.save_fleet([COROLLA])
    payload = superadmin.get(
        "/admin/seed/propose?vehicles=1&part_type=oil_filter"
        "&part_type=air_filter").get_json()
    assert payload["covered"] == 1
    assert payload["count"] == 1
    assert payload["gaps_only"] is True


def test_the_screen_can_ask_for_the_covered_ones_too(app, superadmin):
    _counts(app, [("טויוטה יפן", "COROLLA", 9, 9)])
    _catalog_part(app, "90915-YZZD4", "oil_filter", "טויוטה", "COROLLA", 2005, 2015)
    with app.app_context():
        seed_catalog.save_fleet([COROLLA])
    payload = superadmin.get(
        "/admin/seed/propose?vehicles=1&gaps=0&part_type=oil_filter"
        "&part_type=air_filter").get_json()
    assert payload["count"] == 2
    assert payload["gaps_only"] is False
