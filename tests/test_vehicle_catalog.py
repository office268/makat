"""קטלוג דגמי הרכב ממשרד התחבורה."""
import pytest

from app.models import db
from app.vehicle_catalog import VehicleModel, collapse_records, makes, models_for, upsert

# רשומות אמיתיות מהמאגר, בפורמט המקורי
GOV_RECORDS = [
    {"tozar": "יונדאי", "kinuy_mishari": "ACCENT", "degem_nm": "MC1341",
     "ramat_gimur": "GLI", "shnat_yitzur": 2003, "nefah_manoa": 1495,
     "delek_nm": "בנזין", "merkav": "סדאן", "koah_sus": 105},
    {"tozar": "יונדאי", "kinuy_mishari": "ACCENT", "degem_nm": "MC1341",
     "ramat_gimur": "GLI", "shnat_yitzur": 2005, "nefah_manoa": 1495,
     "delek_nm": "בנזין", "merkav": "סדאן", "koah_sus": 105},
    {"tozar": "יונדאי", "kinuy_mishari": "ACCENT", "degem_nm": "MC1341",
     "ramat_gimur": "GLI", "shnat_yitzur": 2006, "nefah_manoa": 1495,
     "delek_nm": "בנזין", "merkav": "סדאן", "koah_sus": 105},
    {"tozar": "יונדאי", "kinuy_mishari": "GENESIS", "degem_nm": "GC4DD",
     "ramat_gimur": "GLS", "shnat_yitzur": 2012, "nefah_manoa": 3778,
     "delek_nm": "בנזין", "merkav": "סדאן", "koah_sus": 333},
    {"tozar": "יונדאי", "kinuy_mishari": "GENESIS", "degem_nm": "HU6KJ",
     "ramat_gimur": "GLS", "shnat_yitzur": 2012, "nefah_manoa": 3778,
     "delek_nm": "בנזין", "merkav": "סדאן", "koah_sus": 348},
]


def test_collapses_production_years_into_a_range(app):
    """המאגר מחזיק שורה לכל שנה; ההתאמה עובדת בטווחים."""
    rows = collapse_records(GOV_RECORDS)
    accent = next(r for r in rows if r["model"] == "ACCENT")
    assert (accent["year_from"], accent["year_to"]) == (2003, 2006)
    assert accent["engine_volume"] == 1495


def test_same_model_with_different_codes_stays_separate(app):
    """GENESIS עם שני קודי דגם הוא שני דגמים - מנוע והספק שונים."""
    rows = collapse_records(GOV_RECORDS)
    genesis = [r for r in rows if r["model"] == "GENESIS"]
    assert len(genesis) == 2
    assert {r["horsepower"] for r in genesis} == {333, 348}


def test_records_without_make_or_model_are_skipped(app):
    assert collapse_records([
        {"tozar": "", "kinuy_mishari": "X", "shnat_yitzur": 2020},
        {"tozar": "יונדאי", "kinuy_mishari": "", "degem_nm": "", "shnat_yitzur": 2020},
    ]) == []


def test_upsert_is_idempotent(app):
    """הרצה חוזרת של הייבוא לא משכפלת דגמים."""
    rows = collapse_records(GOV_RECORDS)
    with app.app_context():
        created, _updated = upsert(rows)
        assert created == 3          # ACCENT + שני GENESIS
        created_again, _ = upsert(rows)
        assert created_again == 0
        assert VehicleModel.query.count() == 3


def test_upsert_widens_an_existing_year_range(app):
    with app.app_context():
        upsert(collapse_records(GOV_RECORDS))
        upsert(collapse_records([dict(GOV_RECORDS[0], shnat_yitzur=2009)]))
        accent = VehicleModel.query.filter_by(model="ACCENT").first()
        assert (accent.year_from, accent.year_to) == (2003, 2009)


def test_label_distinguishes_models_sharing_a_name(app):
    """בלי קוד דגם והספק, שני ה-GENESIS נראים זהים ברשימה."""
    with app.app_context():
        upsert(collapse_records(GOV_RECORDS))
        labels = {v.label for v in VehicleModel.query.filter_by(model="GENESIS")}
        assert len(labels) == 2
        assert any("GC4DD" in label for label in labels)


def test_lookup_helpers(app):
    with app.app_context():
        upsert(collapse_records(GOV_RECORDS))
        assert makes() == ["יונדאי"]
        assert set(models_for("יונדאי")) == {"ACCENT", "GENESIS"}


def test_api_serves_the_picker(client, app):
    with app.app_context():
        upsert(collapse_records(GOV_RECORDS))
    assert client.get("/api/vehicle-models?makes_only=1").get_json() == ["יונדאי"]
    models = client.get("/api/vehicle-models?models_only=1&make=יונדאי").get_json()
    assert set(models) == {"ACCENT", "GENESIS"}
    rows = client.get("/api/vehicle-models?make=יונדאי&model=ACCENT").get_json()
    assert rows[0]["years"] == "2003-2006"
