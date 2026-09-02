"""כתובות שמגיעות מדף של מישהו אחר.

השליפה החיה מביאה דף מאתר חיצוני, מודל קורא אותו, ומה שחוזר משם נכתב
ל-``href`` ול-``src`` במסך של המכונאי. זו נקודת האמון היחידה במערכת
שבה מחרוזת שגורם שלישי שולט בה מגיעה עד ה-DOM.
"""
import json

import pytest

from app import live_lookup, parts_discovery
from app.catalog_sources import Candidate
from app.models import db

EVIL = "javascript:fetch('/api/parts/1',{method:'DELETE'})"

HOSTILE_SCHEMES = [
    EVIL,
    "JavaScript:alert(1)",          # סכימה אינה תלוית רישיות
    "  javascript:alert(1)",        # רווח מוביל
    "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
    "vbscript:msgbox(1)",
    "file:///etc/passwd",
]


@pytest.mark.parametrize("raw", HOSTILE_SCHEMES)
def test_only_http_urls_survive(raw):
    assert parts_discovery.safe_url(raw) == ""


@pytest.mark.parametrize(
    "raw", ["https://example.com/p/1", "http://example.com/p/1", "HTTPS://EXAMPLE.COM/x"]
)
def test_real_urls_are_kept(raw):
    assert parts_discovery.safe_url(raw) == raw.strip()


def test_validate_strips_a_hostile_url(app):
    """מועמד שעבר את כל שאר הבדיקות עדיין לא מכניס כתובת עוינת."""
    with app.app_context():
        accepted, _ = parts_discovery.validate(
            [{
                "part_number": "OC90", "manufacturer": "MAHLE",
                "confidence": "high", "source_url": EVIL, "image_url": EVIL,
            }],
            "טויוטה", "COROLLA", "oil_filter",
        )
        assert len(accepted) == 1, "המועמד עצמו תקין - רק הכתובת נפסלת"
        assert accepted[0]["source_url"] == ""
        assert accepted[0]["image_url"] == ""


def test_the_unverified_list_is_cleaned_too(app):
    """הרשימה שלא אומתה נבנית מהשורה הגולמית ולא עוברת ב-validate.

    זו דווקא הרשימה שאין לסמוך עליה, ולכן היא הנתיב שחשוב לבדוק.
    """
    def hostile(source, vehicle, part_type, data, **_):
        return [
            Candidate(part_number="OK-1", manufacturer="MAHLE", confidence="high",
                      source_url=EVIL, image_url=EVIL),
            Candidate(part_number="BAD-1", manufacturer="", confidence="low",
                      source_url=EVIL, image_url=EVIL),
        ]

    with app.app_context():
        job = live_lookup.LookupJob(
            plate="1234567", vin_key="vin:X", part_type="oil_filter",
            vehicle=json.dumps({"make": "טויוטה", "model": "COROLLA", "year": 2015}),
            stages=json.dumps(["mock"]),
            results=json.dumps({"results": [], "unverified": []}),
        )
        db.session.add(job)
        db.session.commit()
        live_lookup.run_step(job, runner=hostile)

        payload = job.to_dict()
        rows = (payload["results"] or []) + (payload["unverified"] or [])
        assert rows, "הבדיקה חסרת ערך אם לא הגיעה אף שורה למסך"
        for row in rows:
            assert row["source_url"] == "", row
            assert row["image_url"] == "", row


def test_json_responses_carry_hebrew_as_hebrew(client):
    """‏JSON_AS_ASCII הוסר מ-Flask 2.3 והמפתח נשאר בקונפיג בלי שאיש קורא
    אותו. כל תשובת API יצאה כרצף \\uXXXX - כשישים אחוז יותר בתים."""
    response = client.get("/api/vehicles/makes")
    assert response.status_code == 200
    assert b"\\u05" not in response.data
