"""חיפוש, ייבוא וסטטיסטיקות."""
import io

from app.models import Part
from app.services import (
    export_csv,
    find_by_number,
    import_csv,
    parts_for_vehicle,
    search_parts,
    stats,
)


def test_find_by_number_matches_part_and_cross_ref(app):
    assert find_by_number("TEST-001").part_number == "TEST-001"
    assert find_by_number("04465-02220").part_number == "TEST-001"
    assert find_by_number("nope") is None


def test_search_by_free_text_hits_cross_refs(app):
    assert search_parts(q="04465").count() == 1
    assert search_parts(q="רפידות").count() == 1


def test_search_filters_by_vehicle(app):
    assert search_parts(make="טויוטה", model="COROLLA", year=2015).count() == 1
    assert search_parts(make="טויוטה", year=2020).count() == 0   # מחוץ לטווח השנים
    assert search_parts(make="מאזדה").count() == 0


def test_parts_for_vehicle_requires_matching_type(app):
    vehicle = {"make": "טויוטה יפן", "model": "COROLLA", "year": 2016}
    assert len(parts_for_vehicle(vehicle, "brake_pads_front")) == 1
    assert len(parts_for_vehicle(vehicle, "oil_filter")) == 0


def test_price_with_vat_and_margin(app):
    part = Part.query.first()
    assert part.price_with_vat == 236.0        # 200 * 1.18
    assert part.margin_percent == 30.0         # (200-140)/200


def test_import_creates_then_updates(app):
    csv_text = (
        "part_number,name_he,manufacturer,category,price,stock_qty,part_type,fitments\n"
        "IMP-1,מסנן שמן,Mahle,מנוע / סינון,45,10,oil_filter,מאזדה:MAZDA 3:2014:2019:PE-VPS\n"
    )
    created, updated, errors = import_csv(io.StringIO(csv_text))
    assert (created, updated, errors) == (1, 0, [])
    part = Part.query.filter_by(part_number="IMP-1").first()
    assert part.part_type == "oil_filter"
    assert part.fitments[0].make == "מאזדה"

    created, updated, errors = import_csv(io.StringIO(csv_text.replace(",45,", ",99,")))
    assert (created, updated) == (0, 1)
    assert Part.query.filter_by(part_number="IMP-1").first().price == 99


def test_import_reports_missing_required_fields(app):
    _created, _updated, errors = import_csv(
        io.StringIO("part_number,name_he\n,חלק בלי מקט\nX-1,\n")
    )
    assert len(errors) == 2


def test_export_round_trips_through_import(app):
    text = export_csv(Part.query.all())
    assert "TEST-001" in text
    assert "04465-02220" in text          # מק"ט מקביל נשמר
    assert "טויוטה:COROLLA:2013:2018" in text


def test_stats_counts(app):
    result = stats()
    assert result["parts"] == 1
    assert result["in_stock"] == 1
    assert result["cross_refs"] == 1
