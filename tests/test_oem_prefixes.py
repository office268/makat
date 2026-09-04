"""התחילית אומרת איזה חלק זה, ולפעמים היא סותרת את מה שביקשנו.

הבדיקות כאן נבנו מגיליון של 2,689 מק"טים שנשלפו בפועל, שבו היו 150
שורות שעברו את האימות למרות שהמק"ט היה של חלק אחר לגמרי.
"""
import pytest

from app import oem_prefixes, parts_discovery


# --------------------------------------------------------------------------
# הטבלה עצמה
# --------------------------------------------------------------------------

def test_the_prefix_ignores_hyphens_and_case():
    assert oem_prefixes.prefix_of("43512-02250") == "43512"
    assert oem_prefixes.prefix_of("4351202250") == "43512"
    assert oem_prefixes.prefix_of(" 58302 d3a00 ") == "58302"


def test_a_front_pad_number_asked_for_as_a_filter_is_a_conflict():
    """המקרה שהגיליון חשף: ‏AURIS air_filter = 43512-02250."""
    assert oem_prefixes.conflict("43512-02250", "air_filter") == "brake_disc_front"


def test_rear_pads_sold_as_front_are_the_expensive_mistake():
    """מסנן שהוא דיסק מתגלה בהזמנה. רפידות הפוכות מתגלות במוסך."""
    assert oem_prefixes.conflict("58302-D3A00", "brake_pads_front") == "brake_pads_rear"
    assert oem_prefixes.conflict("04466-42060", "brake_pads_front") == "brake_pads_rear"


def test_the_matching_type_is_not_a_conflict():
    assert oem_prefixes.conflict("58302-D3A00", "brake_pads_rear") is None
    assert oem_prefixes.conflict("04465-42160", "brake_pads_front") is None


def test_one_prefix_can_serve_two_types():
    """‏90919 הוא גם מצת וגם סליל הצתה. ערך יחיד היה פוסל אחד מהם."""
    assert oem_prefixes.conflict("90919-01253", "spark_plug") is None
    assert oem_prefixes.conflict("90919-02258", "ignition_coil") is None
    assert oem_prefixes.conflict("90919-01253", "oil_filter") == "spark_plug"


def test_an_unknown_prefix_passes():
    """הטבלה קטנה בכוונה. החמרה כאן הייתה פוסלת מק"טים תקינים."""
    assert oem_prefixes.conflict("KBP-3053", "brake_pads_rear") is None
    assert oem_prefixes.conflict("B6YS-33-28Z", "brake_pads_front") is None
    assert oem_prefixes.conflict("", "oil_filter") is None
    assert oem_prefixes.conflict(None, "oil_filter") is None


def test_an_unknown_part_type_is_not_judged():
    assert oem_prefixes.conflict("43512-02250", "לא_קיים") is None


def test_the_table_only_names_real_part_types():
    from app.taxonomy import PART_TYPES

    for prefix, types in oem_prefixes.PREFIXES.items():
        assert types, prefix
        for key in types:
            assert key in PART_TYPES, f"{prefix} -> {key}"


def test_the_explanation_names_both_sides():
    text = oem_prefixes.explain("43512-02250", "air_filter")
    assert "43512" in text
    assert "דיסק בלם קדמי" in text
    assert "מסנן אוויר" in text


# --------------------------------------------------------------------------
# השער באימות
# --------------------------------------------------------------------------

def _candidate(number, maker="Toyota", **extra):
    return dict({"part_number": number, "manufacturer": maker,
                 "confidence": "high"}, **extra)


def test_validate_rejects_a_number_whose_prefix_is_another_part(app):
    """היצרן נכון, הביטחון גבוה, אין אזכור של יצרן אחר - וזה חלק אחר."""
    with app.app_context():
        accepted, rejected = parts_discovery.validate(
            [_candidate("43512-02250")], "טויוטה יפן", "AURIS", "air_filter")
    assert accepted == []
    assert "תחילית" in rejected[0][1]


def test_validate_lets_the_matching_prefix_through(app):
    with app.app_context():
        accepted, rejected = parts_discovery.validate(
            [_candidate("17801-0T030")], "טויוטה יפן", "AURIS", "air_filter")
    assert [row["part_number"] for row in accepted] == ["17801-0T030"]
    assert rejected == []


def test_an_aftermarket_part_is_judged_by_its_oe_number(app):
    """מק"ט החלף עצמו אינו מק"ט מקורי, אבל ה-OE שלו כן - וגם הוא יכול
    להיות של הצד השני של הרכב."""
    with app.app_context():
        accepted, rejected = parts_discovery.validate(
            [_candidate("KBP-3053", maker="KAVO PARTS", oe_number="58302-D3A00")],
            "קיה", "SPORTAGE", "brake_pads_front")
    assert accepted == []
    assert "תחילית" in rejected[0][1]


def test_an_aftermarket_part_with_a_matching_oe_number_passes(app):
    with app.app_context():
        accepted, rejected = parts_discovery.validate(
            [_candidate("KBP-3053", maker="KAVO PARTS", oe_number="58302-D3A00")],
            "קיה", "SPORTAGE", "brake_pads_rear")
    assert [row["part_number"] for row in accepted] == ["KBP-3053"]


def test_the_rejection_hint_points_at_the_diagram(app):
    """מק"ט מהתרשים הסמוך הוא הסימן שהמסע נעצר בעמוד קבוצה."""
    with app.app_context():
        parts_discovery.validate(
            [_candidate("43512-02250")], "טויוטה יפן", "AURIS", "air_filter")
    hint = parts_discovery._rejection_hint(
        [("43512-02250", "תחילית 43512 היא של דיסק בלם קדמי, לא מסנן אוויר")])
    assert "תרשים" in hint


# --------------------------------------------------------------------------
# כשחמישה תווים לא מפרידים
# --------------------------------------------------------------------------

def test_seven_characters_separate_a_plug_from_a_coil():
    """‏90919 לבדו מכסה את שניהם. התו השישי-שביעי הוא ההבדל."""
    assert oem_prefixes.conflict("90919-01253", "spark_plug") is None
    assert oem_prefixes.conflict("90919-01253", "ignition_coil") == "spark_plug"
    assert oem_prefixes.conflict("90919-02258", "ignition_coil") is None
    assert oem_prefixes.conflict("90919-02258", "spark_plug") == "ignition_coil"


def test_an_unlisted_ninety_thousand_number_falls_back_to_the_short_prefix():
    """תת-סדרה שאינה בטבלה עדיין מוכרת כהצתה, ולכן שניהם עוברים."""
    assert oem_prefixes.matched_prefix("90919-77777") == "90919"
    assert oem_prefixes.conflict("90919-77777", "spark_plug") is None
    assert oem_prefixes.conflict("90919-77777", "ignition_coil") is None
    assert oem_prefixes.conflict("90919-77777", "oil_filter") == "spark_plug"


def test_the_matched_prefix_is_the_longest_one_in_the_table():
    assert oem_prefixes.matched_prefix("90919-01253") == "9091901"
    assert oem_prefixes.matched_prefix("43512-02250") == "43512"
    assert oem_prefixes.matched_prefix("KBP-3053") == ""


def test_a_rear_shock_number_asked_for_as_a_front_one_is_caught():
    """‏48510/48520 קדמי, 48530/48531 אחורי - ומי שמזמין הפוך מגלה במוסך."""
    assert oem_prefixes.conflict("48520-80507", "shock_absorber_rear") \
        == "shock_absorber_front"
    assert oem_prefixes.conflict("48531-80507", "shock_absorber_front") \
        == "shock_absorber_rear"


def test_a_rear_disc_prefix_also_covers_the_drum_on_a_pickup():
    """‏42431 הוא דיסק אחורי ברכב פרטי ותוף בטנדר. ערך יחיד היה פוסל."""
    assert oem_prefixes.conflict("42431-42050", "brake_disc_rear") is None
    assert oem_prefixes.conflict("42431-42050", "brake_caliper") is None
    assert oem_prefixes.conflict("42431-42050", "air_filter") == "brake_disc_rear"


# --------------------------------------------------------------------------
# הדלת השנייה: ייבוא CSV
# --------------------------------------------------------------------------

def _csv(rows):
    import io
    head = "part_number,name_he,part_type\n"
    return io.StringIO(head + "".join(f"{n},{h},{t}\n" for n, h, t in rows))


def test_the_import_refuses_a_row_whose_prefix_is_another_part(app):
    """הקובץ הוא הדלת השנייה לקטלוג, ועד כה היא לא נבדקה.

    מק"ט של רפידות אחוריות שנרשם כקדמיות נחסם בשליפה החיה - ונכנס
    בייבוא. מי שמזמין לפיו מגלה את הטעות במוסך.
    """
    from app import services
    from app.models import Part

    with app.app_context():
        created, _, errors = services.import_csv(_csv([
            ("58302-D3A00", "רפידות", "brake_pads_front"),
            ("17801-0T030", "מסנן אוויר", "air_filter"),
        ]))
        assert created == 1
        assert len(errors) == 1
        assert "תחילית" in errors[0]
        assert "שורה 2" in errors[0]
        assert Part.query.filter_by(part_number="58302-D3A00").first() is None
        assert Part.query.filter_by(part_number="17801-0T030").first() is not None


def test_the_same_number_with_the_right_type_still_imports(app):
    """המספר תקין - הסיווג היה שגוי. פסילה של המספר עצמו הייתה טעות."""
    from app import services
    from app.models import Part

    with app.app_context():
        created, _, errors = services.import_csv(_csv([
            ("58302-D3A00", "רפידות אחוריות", "brake_pads_rear"),
        ]))
        assert created == 1
        assert errors == []
        assert Part.query.filter_by(part_number="58302-D3A00").first() is not None


def test_a_supplier_price_list_without_part_types_imports_untouched(app):
    """‏explain שותק על תחילית לא מוכרת ועל סוג ריק, ולכן מחירון רגיל
    אינו נפגע מהשער הזה."""
    from app import services

    with app.app_context():
        created, _, errors = services.import_csv(_csv([
            ("KBP-3053", "רפידות KAVO", ""),
            ("B6YS-33-28Z", "רפידות מאזדה", "brake_pads_front"),
        ]))
        assert created == 2
        assert errors == []


def test_58411_is_the_rear_disc_of_hyundai_and_kia():
    """‏הטבלה טענה שזו תחילית של דיסק קדמי, וחסמה 62 שורות תקינות.

    ‏מה שהכריע אינו שם החלף אלא המקבילים שלו: בקטלוג יש 62 מק"טים
    ‏שמספרם מתחיל ב-58411, ו-54 מהם נקראים במפורש "דיסק בלם אחורי".
    ‏שמונת הנותרים נקראים "קדמי" - וגם הם מצליבים אל 58411, לא אל
    ‏51712 שהוא הקדמי. השם ירש את מה שנשאל, המספר אומר מה נמצא.
    """
    from app import oem_prefixes

    assert oem_prefixes.PREFIXES["58411"] == ("brake_disc_rear",)
    assert oem_prefixes.conflict("58411-0U300", "brake_disc_rear") is None
    assert oem_prefixes.conflict("58411-0U300", "brake_disc_front") == "brake_disc_rear"
    # ‏והקדמי נשאר הקדמי - זה לא היפוך של כל המשפחה
    assert oem_prefixes.conflict("51712-1F300", "brake_disc_front") is None


def test_a_rear_hyundai_disc_now_reaches_the_catalog(app):
    """‏מה שהבאג עלה בפועל: שורה נכונה לחלוטין נדחתה בייבוא."""
    from app import services
    from app.models import Part

    with app.app_context():
        created, _, errors = services.import_csv(_csv([
            ("58411-0U300", "דיסק בלם אחורי — יונדאי i20", "brake_disc_rear"),
        ]))
        assert created == 1
        assert errors == []
        assert Part.query.filter_by(part_number="58411-0U300").first() is not None
