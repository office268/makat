"""זיהוי סוג חלק מטקסט חופשי."""
import pytest

from app.identify import identify_from_text
from app.taxonomy import PART_TYPES, all_types


@pytest.mark.parametrize(
    "query,expected",
    [
        ("רפידות קדמיות", "brake_pads_front"),
        ("רפידות בלם אחוריות", "brake_pads_rear"),
        ("פילטר שמן", "oil_filter"),
        ("מסנן אויר", "air_filter"),
        ("דיסק בלם קדמי", "brake_disc_front"),
        ("משאבת מים", "water_pump"),
        ("אמורטיזר קדמי", "shock_absorber_front"),
        ("קטלייזר", "catalytic_converter"),
        ("דינמו", "alternator"),
        ("ז'וינט", "ball_joint"),
    ],
)
def test_identifies_common_hebrew_terms(query, expected):
    results = identify_from_text(query)
    assert results, f"לא זוהה דבר עבור {query!r}"
    assert results[0]["part_type"] == expected


def test_unknown_text_returns_nothing():
    assert identify_from_text("אבטיח בטעם תות") == []


def test_empty_text_returns_nothing():
    assert identify_from_text("") == []
    assert identify_from_text(None) == []


def test_taxonomy_is_well_formed():
    types = all_types()
    assert len(types) == len(PART_TYPES)
    assert all(t["name_he"] and t["category"] for t in types)
