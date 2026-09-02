"""תרשים הפיצוץ: מה שהמכונאי מקבל אחרי המקור הראשון.

Laximo מחזיר את המק"ט המקורי לשלדה, ולעיתים גם את תרשים הפיצוץ -
הסכמה שבה החלק מסומן במקומו ברכב. עד היום שניהם נדחסו לאותו שדה,
והתרשים הוצג כתמונה ממוזערת בגודל 64 פיקסלים.
"""
import json

from app import live_lookup, parts_discovery
from app.catalog_sources import Candidate
from app.models import Part, db

DIAGRAM = "https://cdn.example/laximo/exploded-42.png"
PHOTO = "https://cdn.example/oc90.jpg"
OE = "04152-YZZA1"

VEHICLE = {"make": "טויוטה", "model": "COROLLA", "year": 2015,
           "vin": "JTDBR32E560012345", "engine_code": "2ZR-FAE", "plate": "1234567"}


def _laximo(source, vehicle, part_type, data, **_):
    return [Candidate(
        part_number=OE, manufacturer="TOYOTA", tier="oem", confidence="high",
        oe_number=OE, image_url=PHOTO, diagram_url=DIAGRAM,
    )]


def _job():
    job = live_lookup.LookupJob(
        plate="1234567", vin_key=live_lookup.vin_key(VEHICLE),
        part_type="oil_filter", vehicle=json.dumps(VEHICLE),
        stages=json.dumps(["laximo", "tecdoc"]),
        results=json.dumps({"results": [], "unverified": []}),
    )
    db.session.add(job)
    db.session.commit()
    return job


def test_the_diagram_reaches_the_screen_separately_from_the_photo(app):
    with app.app_context():
        job = _job()
        live_lookup.run_step(job, runner=_laximo)
        assert job.awaiting_approval, "עוצרים אחרי Laximo"

        row = job.to_dict()["results"][0]
        assert row["part_number"] == OE
        assert row["diagram_url"] == DIAGRAM
        assert row["image_url"] == PHOTO, "התצלום נשאר שדה נפרד"


def test_a_hostile_diagram_url_is_filtered_like_any_other(app):
    """אותו סינון סכימות כמו לשאר הכתובות - הן מגיעות מאותו מקום."""
    def hostile(source, vehicle, part_type, data, **_):
        return [Candidate(part_number=OE, manufacturer="TOYOTA", tier="oem",
                          confidence="high", diagram_url="javascript:alert(1)")]

    with app.app_context():
        job = _job()
        live_lookup.run_step(job, runner=hostile)
        assert job.to_dict()["results"][0]["diagram_url"] == ""


def test_the_diagram_is_kept_on_the_part_so_the_catalog_answer_has_it(app):
    """בלי זה החיפוש הבא נענה מהקטלוג המקומי - ומאבד את התרשים."""
    with app.app_context():
        job = _job()
        live_lookup.run_step(job, runner=_laximo)
        part = Part.query.filter_by(part_number=OE).one()
        assert part.diagram_url == DIAGRAM
        assert part.image_url == PHOTO


def test_the_catalog_answer_carries_the_diagram(client, app):
    """המסלול שנענה מהקטלוג בלי לצאת לרשת מציג את אותו תרשים."""
    from app.routes.identify import _catalog_row

    with app.app_context():
        job = _job()
        live_lookup.run_step(job, runner=_laximo)
        part = Part.query.filter_by(part_number=OE).one()
        assert _catalog_row(part)["diagram_url"] == DIAGRAM


def test_manual_edits_are_not_overwritten_by_a_later_lookup(app):
    """מק"ט שכבר קיבל תרשים לא נדרס - אותו כלל כמו התמונה."""
    with app.app_context():
        db.session.add(Part(part_number=OE, name_he="מסנן",
                            diagram_url="https://mine.example/keep.png"))
        db.session.commit()
        job = _job()
        live_lookup.run_step(job, runner=_laximo)
        part = Part.query.filter_by(part_number=OE).one()
        assert part.diagram_url == "https://mine.example/keep.png"


def test_validate_passes_the_diagram_through(app):
    with app.app_context():
        accepted, _ = parts_discovery.validate(
            [{"part_number": OE, "manufacturer": "TOYOTA", "confidence": "high",
              "diagram_url": DIAGRAM}],
            "טויוטה", "COROLLA", "oil_filter",
        )
        assert accepted[0]["diagram_url"] == DIAGRAM


def test_a_partial_csv_does_not_wipe_the_diagram(app, org_id):
    """אותו כלל כמו שאר שדות הקטלוג - עמודה שלא נמסרה אינה נמחקת."""
    import io as _io

    from app import services

    with app.app_context():
        services.import_csv(
            _io.StringIO(f"part_number,name_he,diagram_url\nD-1,חלק,{DIAGRAM}\n"),
            organization_id=org_id,
        )
        services.import_csv(
            _io.StringIO("part_number,name_he,price\nD-1,חלק,50\n"),
            organization_id=org_id,
        )
        db.session.expire_all()
        assert Part.query.filter_by(part_number="D-1").one().diagram_url == DIAGRAM


# --------------------------------------------------------------------------
# התרשים מהמקור שבאמת רץ
# --------------------------------------------------------------------------

def test_the_vin_source_asks_the_model_for_a_diagram():
    """הפיצ'ר היה מחובר ל-laximo בלבד, שאינו רץ - ולכן מעולם לא ירה.

    דווקא במקור הזה התרשים זמין כמעט תמיד: המסע בקטלוג יצרן נגמר
    בעמוד התרשים, כי שם יושבים המק"טים.
    """
    from app.catalog_sources import epc_vin

    prompt = epc_vin.build_prompt(
        {"make": "טויוטה יפן", "model": "COROLLA", "vin": "X"},
        "oil_filter", "דף", "https://a.com/", 0,
    )
    assert "diagram_url" in prompt
    # ושהמודל יידע להבדיל בינו לבין תצלום המוצר
    assert "תרשים פיצוץ הוא הסכמה" in prompt
    assert "לא תצלום" in prompt


def test_the_diagram_travels_from_the_model_to_the_candidate():
    from app.catalog_sources import epc_vin

    from test_catalog_real_sources import FakeClient

    found = epc_vin.EpcVinSource().lookup(
        {"make": "טויוטה יפן", "model": "COROLLA", "year": 2011,
         "vin": "JTNBV58E20J147563", "engine_code": "1ZR"},
        "oil_filter",
        fetcher=lambda url, timeout=None: "<html><body>x</body></html>",
        client=FakeClient({
            "parts": [{"oe_number": "90915-YZZD4", "name": "Oil filter",
                       "image_url": "https://img.example/photo.jpg",
                       "diagram_url": "https://img.example/exploded.png",
                       "confidence": "high"}],
            "next_url": "", "vehicle_confirmed": True,
        }),
    )
    assert len(found) == 1
    # שני השדות נפרדים, ולא נדרסים זה בזה
    assert found[0].image_url == "https://img.example/photo.jpg"
    assert found[0].diagram_url == "https://img.example/exploded.png"
    assert found[0].as_row()["diagram_url"] == "https://img.example/exploded.png"


def test_a_missing_diagram_is_simply_empty():
    """רוב העמודים לא ייתנו תרשים, וזו לא שגיאה."""
    from app.catalog_sources import epc_vin

    from test_catalog_real_sources import FakeClient

    found = epc_vin.EpcVinSource().lookup(
        {"make": "טויוטה יפן", "model": "COROLLA", "vin": "JTNBV58E20J147563"},
        "oil_filter",
        fetcher=lambda url, timeout=None: "<html><body>x</body></html>",
        client=FakeClient({"parts": [{"oe_number": "90915-YZZD4"}],
                           "next_url": "", "vehicle_confirmed": True}),
    )
    assert found[0].diagram_url == ""
