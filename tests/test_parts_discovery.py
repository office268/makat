"""גילוי מק"טים מהאינטרנט.

התוצאות נכנסות לקטלוג בלי סקירה אנושית, ולכן האימות כאן הוא ההגנה
היחידה - וזה מה שנבדק הכי לעומק.
"""
import pytest

from app import parts_discovery as pd
from app.models import Part, db


def candidate(number="X1", maker="MANN-FILTER", confidence="high", **extra):
    row = {"part_number": number, "manufacturer": maker,
           "confidence": confidence, "note": "", "oe_number": "", "oe_brand": ""}
    row.update(extra)
    return row


# ---- אימות: השומרים ----


def test_accepts_a_clean_candidate(app):
    with app.app_context():
        ok, bad = pd.validate([candidate()], "טויוטה", "COROLLA", "oil_filter")
        assert len(ok) == 1 and bad == []
        assert ok[0]["make"] == "טויוטה"


def test_rejects_a_part_belonging_to_another_marque(app):
    """המקרה שנתפס בפועל: חלק של CHERY בעמוד של קורולה."""
    with app.app_context():
        rows = [candidate(number="F300201", note="CHERY AMULET (01.03~|1.8L)")]
        ok, bad = pd.validate(rows, "טויוטה", "COROLLA", "oil_filter")
        assert ok == []
        assert "chery" in bad[0][1].lower()


def test_rejects_an_oe_number_from_another_marque(app):
    """מסנן עם OE של מרצדס שהופיע בעמוד של סקודה."""
    with app.app_context():
        rows = [candidate(number="KL 912", oe_number="A651 090 2952",
                          oe_brand="Mercedes-Benz")]
        ok, bad = pd.validate(rows, "סקודה", "OCTAVIA", "oil_filter")
        assert ok == []
        assert "mercedes" in bad[0][1].lower()


def test_the_requested_marque_is_not_treated_as_foreign(app):
    """אזכור של היצרן המבוקש עצמו הוא תקין, לא סיבת פסילה."""
    with app.app_context():
        rows = [candidate(oe_brand="Toyota", note="fits Toyota Corolla")]
        ok, _ = pd.validate(rows, "טויוטה", "COROLLA", "oil_filter")
        assert len(ok) == 1


@pytest.mark.parametrize("row, reason", [
    (candidate(number=""), 'חסר מק"ט'),
    (candidate(maker=""), "חסר יצרן"),
    (candidate(confidence="low"), "לא היה בטוח"),
    (candidate(number="Z" * 90), "ארוך מדי"),
])
def test_rejects_malformed_candidates(app, row, reason):
    with app.app_context():
        ok, bad = pd.validate([row], "טויוטה", "COROLLA", "oil_filter")
        assert ok == []
        assert reason in bad[0][1]


def test_rejects_an_unknown_part_type(app):
    with app.app_context():
        ok, bad = pd.validate([candidate()], "טויוטה", "COROLLA", "not_a_type")
        assert ok == []


def test_drops_duplicates_within_one_answer(app):
    with app.app_context():
        ok, bad = pd.validate(
            [candidate(number="W 67/1"), candidate(number="w 67/1")],
            "טויוטה", "COROLLA", "oil_filter")
        assert len(ok) == 1
        assert "כפול" in bad[0][1]


# ---- פירוק תשובת המודל ----


@pytest.mark.parametrize("text", [
    '{"parts": [{"part_number": "A"}]}',
    'הנה התוצאה:\n```json\n{"parts": [{"part_number": "A"}]}\n```',
    'טקסט לפני {"parts": [{"part_number": "A"}]} וטקסט אחרי',
])
def test_extracts_json_from_any_wrapping(text):
    assert pd._json_from(text)["parts"][0]["part_number"] == "A"


def test_broken_json_returns_none():
    assert pd._json_from("אין כאן JSON") is None
    assert pd._json_from('{"parts": [') is None


# ---- כתיבה לקטלוג ----


def test_saved_part_carries_fitment_cross_ref_and_provenance(app):
    with app.app_context():
        rows, _ = pd.validate(
            [candidate(number="NEW-1", oe_number="90915-YZZJ1", oe_brand="Toyota")],
            "טויוטה", "COROLLA", "oil_filter")
        created, updated = pd.save(rows)
        assert (created, updated) == (1, 0)

        part = Part.query.filter_by(part_number="NEW-1").one()
        assert part.part_type == "oil_filter"
        assert [(f.make, f.model) for f in part.fitments] == [("טויוטה", "COROLLA")]
        assert [r.ref_number for r in part.cross_refs] == ["90915-YZZJ1"]
        assert "חיפוש אינטרנט אוטומטי" in part.notes


def test_second_run_adds_a_fitment_instead_of_duplicating(app):
    """אותו מק"ט לדגם אחר - שורה אחת, שתי התאמות."""
    with app.app_context():
        rows, _ = pd.validate([candidate(number="SHARED")], "קיה", "SPORTAGE", "oil_filter")
        pd.save(rows)
        rows, _ = pd.validate([candidate(number="SHARED")], "יונדאי", "i20", "oil_filter")
        created, updated = pd.save(rows)
        assert (created, updated) == (0, 1)

        part = Part.query.filter_by(part_number="SHARED").one()
        assert {f.model for f in part.fitments} == {"SPORTAGE", "i20"}


# ---- העבודה עצמה ----


def fake_search(results):
    def searcher(make, model, part_type):
        return results
    return searcher


def test_job_walks_its_targets_one_at_a_time(app):
    with app.app_context():
        job = pd.start_job([["טויוטה", "COROLLA", "oil_filter"],
                            ["טויוטה", "COROLLA", "air_filter"]])
        pd.run_step(job, searcher=fake_search([candidate(number="A1")]))
        assert job.cursor == 1 and job.is_running and job.created == 1

        pd.run_step(job, searcher=fake_search([candidate(number="A2")]))
        assert job.cursor == 2 and job.status == pd.DiscoveryJob.DONE
        assert job.created == 2


def test_a_failing_search_moves_on_instead_of_stalling(app):
    """כשל במטרה אחת לא תוקע את כל ההרצה."""
    def explode(make, model, part_type):
        raise RuntimeError("מכסת API נגמרה")

    with app.app_context():
        job = pd.start_job([["טויוטה", "COROLLA", "oil_filter"]])
        pd.run_step(job, searcher=explode)
        assert job.cursor == 1
        assert "מכסת API" in job.error


def test_rejections_are_written_to_the_log(app):
    with app.app_context():
        job = pd.start_job([["טויוטה", "COROLLA", "oil_filter"]])
        pd.run_step(job, searcher=fake_search([
            candidate(number="GOOD"),
            candidate(number="BAD", note="CHERY AMULET"),
        ]))
        assert job.created == 1 and job.rejected == 1
        assert "BAD" in job.log and "chery" in job.log.lower()


def test_cancelled_job_stops_working(app):
    with app.app_context():
        job = pd.start_job([["טויוטה", "COROLLA", "oil_filter"]])
        pd.cancel_job(job)
        pd.run_step(job, searcher=fake_search([candidate(number="NOPE")]))
        assert Part.query.filter_by(part_number="NOPE").first() is None


# ---- הרשאות ומסך ----


# ---- שם היצרן שנשמר בהתאמה ----


@pytest.mark.parametrize("selected, stored", [
    ("פיג'ו צרפת", "פיג'ו"),
    ("טויוטה יפן", "טויוטה"),
    ("סקודה", "סקודה"),
    ("  יונדאי קוריאה  ", "יונדאי"),
])
def test_fitment_make_is_the_first_word(selected, stored):
    """החיפוש לפי רישוי משווה מול המילה הראשונה בלבד."""
    assert pd.fitment_make(selected) == stored


def test_saved_part_is_findable_by_plate_lookup(app):
    """הכשל השקט: מק"ט בקטלוג שהחיפוש לפי רישוי לא מוצא.

    הרשימה נטענת מקטלוג משרד התחבורה, ושם השם עשוי לכלול מדינה.
    אם הוא נשמר כך, ההשוואה מול "פיג'ו" נכשלת והמק"ט אבוד.
    """
    from app import services

    with app.app_context():
        rows, _ = pd.validate([candidate(number="FIND-ME")],
                              "פיג'ו צרפת", "5008", "oil_filter")
        pd.save(rows)
        vehicle = {"make": "פיג'ו צרפת", "model": "5008", "year": 2020}
        found = services.parts_for_vehicle(vehicle, "oil_filter")
        assert [p.part_number for p in found] == ["FIND-ME"]


def test_marque_guard_still_works_with_a_country_suffix(app):
    """אזכור טויוטה תקין גם כשנבחר 'טויוטה יפן'."""
    with app.app_context():
        ok, _ = pd.validate([candidate(oe_brand="Toyota")],
                            "טויוטה יפן", "COROLLA", "oil_filter")
        assert len(ok) == 1


# ---- הרשאות ומסך ----


def test_anonymous_is_sent_to_login(app):
    # לקוח נפרד: auth_client ב-conftest מחבר את אותו client עצמו
    assert app.test_client().get("/admin/discovery").status_code == 302


def test_manager_without_superadmin_is_forbidden(auth_client):
    assert auth_client.get("/admin/discovery").status_code == 403
    for path in ("/admin/discovery/start", "/admin/discovery/step",
                 "/admin/discovery/cancel"):
        assert auth_client.post(path).status_code == 403


def test_start_without_a_key_says_so(app, client):
    """בלי מפתח, המסך אומר את זה במקום להיכשל בשקט."""
    app.config["SUPERADMIN_EMAILS"] = frozenset({"fixture@t.test"})
    client.post("/login", data={"phone": "0500000001"})
    html = client.get("/admin/discovery").get_data(as_text=True)
    assert "ANTHROPIC_API_KEY" in html
    # רשימות בחירה, לא טקסט חופשי, ואפשרות לסמן הכל
    assert '<select class="form-select mb-3" name="make"' in html
    assert '<select class="form-select mb-3" name="model"' in html
    assert 'id="select-all"' in html
    response = client.post("/admin/discovery/start",
                           data={"make": "טויוטה", "model": "COROLLA",
                                 "part_type": "oil_filter"})
    assert response.status_code == 400
    assert "ANTHROPIC_API_KEY" in response.get_json()["error"]


# ---- תכנון: מה ירוץ כששדה נשאר ריק ----

def _seed_models(app):
    """קטלוג דגמים קטן, כדי שברירת המחדל תהיה לה ממה לבחור."""
    from app.vehicle_catalog import VehicleModel

    counts = {("טויוטה", "COROLLA"): 4, ("טויוטה", "YARIS"): 3,
              ("טויוטה", "PRIUS"): 1, ("מאזדה", "MAZDA 3"): 2,
              ("מאזדה", "CX-5"): 2, ("סקודה", "OCTAVIA"): 1}
    for (make, model), amount in counts.items():
        for index in range(amount):
            db.session.add(VehicleModel(make=make, model=model,
                                        model_code=f"{model}-{index}"))
    db.session.commit()


def test_all_three_fields_empty_falls_back_to_a_sample(app):
    """הבקשה: לחפש בלי למלא כלום. התוצאה חייבת להיות מדגם, לא כל המאגר."""
    with app.app_context():
        _seed_models(app)
        targets, capped = pd.plan_targets()
        assert not capped
        assert len(targets) == (pd.DEFAULT_MAKES * pd.DEFAULT_MODELS
                                * len(pd.DEFAULT_PART_TYPES))
        # היצרנים הנפוצים ביותר בקטלוג הזה, ורק סוגי חלקים מוכרים
        assert {t[0] for t in targets} == {"טויוטה", "מאזדה"}
        assert {t[2] for t in targets} <= set(pd.DEFAULT_PART_TYPES)


def test_make_only_expands_to_its_popular_models(app):
    with app.app_context():
        _seed_models(app)
        targets, _ = pd.plan_targets(make="טויוטה", part_types=["oil_filter"])
        assert targets == [["טויוטה", "COROLLA", "oil_filter"],
                           ["טויוטה", "YARIS", "oil_filter"]]


def test_model_without_a_make_plans_nothing(app):
    """דגם לבדו לא ניתן להתאמה חד-משמעית, ולכן אין מה להריץ."""
    with app.app_context():
        _seed_models(app)
        assert pd.plan_targets(model="COROLLA") == ([], False)


def test_both_fields_chosen_keeps_exactly_that_pair(app):
    with app.app_context():
        _seed_models(app)
        targets, _ = pd.plan_targets("טויוטה", "COROLLA", ["oil_filter", "air_filter"])
        assert targets == [["טויוטה", "COROLLA", "oil_filter"],
                           ["טויוטה", "COROLLA", "air_filter"]]


def test_unknown_part_types_fall_back_to_the_defaults(app):
    """סוג שאינו בטקסונומיה לא הופך למטרה - ולא משאיר את הרשימה ריקה."""
    with app.app_context():
        _seed_models(app)
        targets, _ = pd.plan_targets("טויוטה", "COROLLA", ["לא-קיים"])
        assert [t[2] for t in targets] == pd.DEFAULT_PART_TYPES


def test_the_plan_is_capped(app, monkeypatch):
    """תקרה קשיחה: לחיצה אחת לא יכולה לפתוח חשבון בלתי צפוי."""
    monkeypatch.setattr(pd, "MAX_TARGETS", 3)
    with app.app_context():
        _seed_models(app)
        targets, capped = pd.plan_targets("טויוטה", "COROLLA")
        assert len(targets) == 3 and capped


def test_an_empty_vehicle_catalog_plans_nothing(app):
    """בלי קטלוג דגמים אין ממה למלא ברירת מחדל."""
    with app.app_context():
        assert pd.plan_targets() == ([], False)


# ---- תצוגה מקדימה בשרת ----

def _login_superadmin(app, client):
    app.config["SUPERADMIN_EMAILS"] = frozenset({"fixture@t.test"})
    client.post("/login", data={"phone": "0500000001"})


def test_plan_endpoint_describes_what_will_run(app, client):
    with app.app_context():
        _seed_models(app)
    _login_superadmin(app, client)
    plan = client.get("/admin/discovery/plan").get_json()
    assert plan["count"] == (pd.DEFAULT_MAKES * pd.DEFAULT_MODELS
                             * len(pd.DEFAULT_PART_TYPES))
    assert plan["capped"] is False
    assert plan["max"] == pd.MAX_TARGETS
    assert len(plan["sample"]) == 6            # דוגמה, לא הרשימה כולה
    assert "טויוטה COROLLA" in plan["sample"][0]


def test_plan_endpoint_is_superadmin_only(auth_client):
    assert auth_client.get("/admin/discovery/plan").status_code == 403


def test_starting_with_empty_fields_opens_a_job(app, client, monkeypatch):
    """הבקשה מקצה לקצה: שלושה שדות ריקים, ובכל זאת רצה עבודה."""
    monkeypatch.setattr(pd, "discovery_available", lambda: True)
    with app.app_context():
        _seed_models(app)
    _login_superadmin(app, client)
    payload = client.post("/admin/discovery/start", data={}).get_json()
    assert payload["job"]["total"] == (pd.DEFAULT_MAKES * pd.DEFAULT_MODELS
                                       * len(pd.DEFAULT_PART_TYPES))
    assert payload["job"]["status"] == "running"


def test_starting_with_nothing_to_plan_says_so(app, client, monkeypatch):
    """קטלוג דגמים ריק - הודעה מפורשת במקום עבודה ריקה שמסתיימת מיד."""
    monkeypatch.setattr(pd, "discovery_available", lambda: True)
    _login_superadmin(app, client)
    response = client.post("/admin/discovery/start", data={})
    assert response.status_code == 400
    assert "לא נמצאו דגמים" in response.get_json()["error"]


# ---- סקירה: מה נכנס לקטלוג ומה חשוד בו ----

def _discovered(number="DISC-1", make="טויוטה", model="COROLLA",
                part_type="oil_filter", maker="MANN-FILTER", note=None,
                oe_number="", oe_brand=""):
    """מק"ט כאילו הגיע מהחיפוש, בלי לעבור דרך המודל."""
    from app.models import CrossReference, Fitment
    from app.services import get_or_create_manufacturer

    part = Part(part_number=number, name_he="חלק", part_type=part_type,
                notes=note if note is not None else f"{pd.SOURCE_NOTE} https://example.test/x")
    if maker:
        part.manufacturer = get_or_create_manufacturer(maker)
    if make:
        part.fitments.append(Fitment(make=make, model=model))
    if oe_number:
        part.cross_refs.append(
            CrossReference(ref_type="OEM", ref_number=oe_number, ref_brand=oe_brand)
        )
    db.session.add(part)
    db.session.commit()
    return part


def test_discovered_parts_are_the_ones_marked(app):
    """הרשימה היא בדיוק מה שהחיפוש נגע בו, לא כל הקטלוג."""
    with app.app_context():
        _discovered("DISC-1")
        db.session.add(Part(part_number="HAND-1", name_he="ידני",
                            notes="מקור: קטלוג מקוון לא רשמי"))
        db.session.commit()
        assert [p.part_number for p in pd.discovered_parts()] == ["DISC-1"]


def test_a_clean_part_raises_no_flag(app):
    with app.app_context():
        assert pd.review_flags(_discovered()) == []


def test_a_part_without_a_fitment_is_flagged(app):
    """הכשל השקט: בקטלוג, ולא יימצא לעולם בחיפוש לפי רישוי."""
    with app.app_context():
        flags = pd.review_flags(_discovered("NOFIT", make=None))
        assert any("בלי התאמה" in flag["text"] for flag in flags)
        # אימות מול הרשת לא יכול לפתור חוסר התאמה, ולכן זה מסומן כמבני
        assert pd.structural(flags) and pd.suspect(flags)


def test_an_oe_number_of_another_marque_is_flagged(app):
    """בדיוק מה שנתפס ביד: מק"ט מקביל של הונדה על חלף של קיה."""
    with app.app_context():
        flags = pd.review_flags(_discovered(
            "KIA-1", make="קיה", model="SPORTAGE",
            oe_number="15400-PH1-F03", oe_brand="Honda"))
        assert any("יצרן אחר" in flag["text"] and "honda" in flag["text"]
                   for flag in flags)
        # את זה המודל דווקא יכול להכריע, ולכן הוא לא מבני
        assert pd.suspect(flags) and not pd.structural(flags)


def test_the_matching_marque_is_not_flagged(app):
    with app.app_context():
        assert pd.review_flags(_discovered(
            "TOY-1", oe_number="90915-YZZJ1", oe_brand="Toyota")) == []


def test_missing_maker_and_unknown_type_are_flagged(app):
    with app.app_context():
        flags = pd.review_flags(_discovered("BAD-1", maker=None, part_type="לא-קיים"))
        assert any("בלי יצרן" in flag["text"] for flag in flags)
        assert any("סוג חלק לא מוכר" in flag["text"] for flag in flags)


def test_a_part_that_predates_the_search_is_flagged(app):
    """מחיקה של כזה מוחקת גם עבודה ידנית, ולכן זה נאמר במפורש."""
    with app.app_context():
        part = _discovered("BOTH-1", note=f"מקור: קטלוג מקוון (AUTODOC) | {pd.SOURCE_NOTE}")
        flags = pd.review_flags(part)
        assert any("היה בקטלוג" in flag["text"] for flag in flags)
        # אזהרה, לא סיבה למחוק - ולכן השורה לא נבחרת מראש
        assert not pd.suspect(flags)


def test_the_source_url_comes_out_of_the_note(app):
    with app.app_context():
        assert pd.source_url_of(_discovered()) == "https://example.test/x"
        assert pd.source_url_of(_discovered("NOURL", note=pd.SOURCE_NOTE)) is None


def test_saving_does_not_overwrite_an_existing_note(app):
    """מק"ט שנאסף קודם ועודכן בחיפוש שומר על סימון המקור שלו."""
    with app.app_context():
        db.session.add(Part(part_number="KEEP-1", name_he="ידני",
                            part_type="oil_filter", notes="מקור: קטלוג מקוון (AUTODOC)"))
        db.session.commit()
        rows, _ = pd.validate([candidate(number="KEEP-1")],
                              "טויוטה", "COROLLA", "oil_filter")
        created, updated = pd.save(rows)
        part = Part.query.filter_by(part_number="KEEP-1").one()
        assert (created, updated) == (0, 1)
        assert pd.CATALOG_MARK in part.notes
        assert pd.SOURCE_MARK in part.notes


def test_the_review_screen_lists_and_flags(app, client):
    with app.app_context():
        _discovered("SHOW-1")
        _discovered("SHOW-2", make=None)
    _login_superadmin(app, client)
    html = client.get("/admin/discovery/review").get_data(as_text=True)
    assert "SHOW-1" in html and "SHOW-2" in html
    assert "1 עם סימן שאלה" in html


def test_deleting_removes_the_part_and_its_fitments(app, client):
    from app.models import Fitment

    with app.app_context():
        part = _discovered("DROP-1")
        part_id = part.id
    _login_superadmin(app, client)
    payload = client.post("/admin/discovery/delete",
                          data={"part_id": part_id}).get_json()
    assert payload["deleted"] == ["DROP-1"]
    with app.app_context():
        assert Part.query.filter_by(part_number="DROP-1").first() is None
        assert Fitment.query.filter_by(part_id=part_id).count() == 0


def test_review_and_delete_need_a_superadmin(auth_client):
    assert auth_client.get("/admin/discovery/review").status_code == 403
    for path in ("/admin/discovery/delete", "/admin/discovery/verify"):
        assert auth_client.post(path).status_code == 403


def test_verify_asks_the_model_about_one_part(app, client, monkeypatch):
    """אימות מחזיר פסק דין וסיבה, ומק"ט אחד לכל בקשה."""
    monkeypatch.setattr(pd, "discovery_available", lambda: True)
    seen = []

    class FakeClient:
        class messages:
            @staticmethod
            def create(**kwargs):
                seen.append(kwargs["messages"][0]["content"])

                class Block:
                    type = "text"
                    text = ('{"verdict": "not_fits", "reason": "OE של הונדה",'
                            ' "source_url": "https://example.test/y"}')

                class Response:
                    content = [Block()]

                return Response()

    with app.app_context():
        part = _discovered("VER-1", make="קיה", model="SPORTAGE")
        part_id = part.id
        verdict = pd.verify(part, client=FakeClient())
    assert verdict["verdict"] == "not_fits"
    assert verdict["reason"] == "OE של הונדה"
    assert "SPORTAGE" in seen[0] and "VER-1" in seen[0]

    _login_superadmin(app, client)
    monkeypatch.setattr(pd, "verify", lambda part, client=None: {
        "verdict": "fits", "reason": "", "source_url": ""})
    assert client.post("/admin/discovery/verify",
                       data={"part_id": part_id}).get_json()["verdict"] == "fits"


def test_verify_reports_a_failure_instead_of_a_500(app, client, monkeypatch):
    monkeypatch.setattr(pd, "discovery_available", lambda: True)
    with app.app_context():
        part_id = _discovered("VER-2").id
    _login_superadmin(app, client)

    def explode(part, client=None):
        raise RuntimeError("מכסה נגמרה")

    monkeypatch.setattr(pd, "verify", explode)
    response = client.post("/admin/discovery/verify", data={"part_id": part_id})
    assert response.status_code == 502
    assert "מכסה" in response.get_json()["error"]
