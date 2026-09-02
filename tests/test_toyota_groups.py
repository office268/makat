"""הקיצור לתרשים: מה הוא בונה, ומתי הוא מסרב לבנות.

הבדיקות כאן נשענות על כתובות אמיתיות מהקטלוג החי - עמוד רכב של RAV4
‏2015 בשוק NA ושל היילקס 2025 בשוק GENERAL - כי כל ערכה של המפה תלויה
בכך שמבנה הכתובת הזה הוא מה שנחשב.
"""
from app.catalog_sources import toyota_groups
from app.taxonomy import PART_TYPES

RAV4 = (
    "https://partsouq.com/en/catalog/toyota/vehicle/NA/2015/RAV4-JPP/"
    "ASA44L-ANTGKA/category/2/vin/JTMBFREV50J063539"
)
HILUX = (
    "https://partsouq.com/en/catalog/toyota/vehicle/GENERAL/2025/"
    "HILUX-DOUBLE-CAB/GUN126L-DGFHXF/category/1/vin/8AJBA3CD1T7957303"
)


def test_builds_the_diagram_url_for_a_mapped_part_type():
    assert toyota_groups.diagram_url(RAV4, "brake_pads_front") == (
        "https://partsouq.com/en/catalog/toyota/diagram/NA/2015/RAV4-JPP/"
        "ASA44L-ANTGKA/category/2/diagram/4705/vin/JTMBFREV50J063539"
    )


def test_rewrites_the_category_and_not_only_the_diagram():
    """הקבוצה יושבת בקטגוריה משלה, שאינה בהכרח זו שהגענו ממנה."""
    # הכתובת הנתונה היא קטגוריה 2 (שסי), ומסנן שמן יושב בקטגוריה 1.
    url = toyota_groups.diagram_url(RAV4, "oil_filter")
    assert "/category/1/diagram/1502/" in url


def test_keeps_the_vehicle_identity_untouched():
    """שוק, שנה, דגם וקוד דגם הם הרכב עצמו - קיצור שמשנה אותם שיקר."""
    url = toyota_groups.diagram_url(HILUX, "oil_filter")
    assert "/GENERAL/2025/HILUX-DOUBLE-CAB/GUN126L-DGFHXF/" in url
    assert url.endswith("/vin/8AJBA3CD1T7957303")


def test_no_shortcut_without_a_known_group():
    """סוג חלק שאין לו קבוצה במפה ממשיך במסע הרגיל, ולא מנחש קבוצה."""
    assert toyota_groups.diagram_url(RAV4, "unmapped_part_type") is None


def test_no_shortcut_for_another_catalog():
    """הקבוצות הן של טויוטה. קטלוג אחר מקבל None ולא כתובת שבורה."""
    other = RAV4.replace("/catalog/toyota/", "/catalog/kia/")
    assert toyota_groups.diagram_url(other, "brake_pads_front") is None


def test_no_shortcut_from_a_diagram_page():
    """כתובת תרשים אינה עמוד רכב - בלי זה הקיצור היה לולאה."""
    diagram = toyota_groups.diagram_url(RAV4, "brake_pads_front")
    assert toyota_groups.diagram_url(diagram, "brake_pads_front") is None


def test_works_without_a_vin_segment():
    """עמוד רכב שהגענו אליו בלי שלדה עדיין מוביל לתרשים."""
    url = toyota_groups.diagram_url(RAV4.split("/vin/")[0], "brake_pads_front")
    assert url.endswith("/category/2/diagram/4705")


def test_every_mapped_key_is_a_real_part_type():
    """מפתח שאינו בטקסונומיה לא ייבחר לעולם - קבוצה מתה במפה."""
    unknown = set(toyota_groups.GROUPS) - set(PART_TYPES)
    assert not unknown, f"קבוצות לסוגי חלק שאינם קיימים: {sorted(unknown)}"


def test_the_map_covers_the_wear_parts_the_app_is_built_around():
    """החלקים המתכלים הם רוב השאילתות, וקיצור שמדלג עליהם חסר ערך."""
    missing = [
        key for key in
        ("brake_pads_front", "brake_disc_front", "oil_filter", "air_filter",
         "spark_plug", "shock_absorber_front", "alternator", "water_pump")
        if key not in toyota_groups.GROUPS
    ]
    assert not missing, f"חסרות קבוצות ל: {missing}"
