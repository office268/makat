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
    client.post("/login", data={"email": "fixture@t.test", "password": "password123"})
    html = client.get("/admin/discovery").get_data(as_text=True)
    assert "ANTHROPIC_API_KEY" in html
    response = client.post("/admin/discovery/start",
                           data={"make": "טויוטה", "model": "COROLLA",
                                 "part_type": "oil_filter"})
    assert response.status_code == 400
    assert "ANTHROPIC_API_KEY" in response.get_json()["error"]
