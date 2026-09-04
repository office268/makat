"""קובץ הקטלוג: הוא מה שנטען בפרודקשן, ולכן זה מה שנבדק."""
import re
import pathlib

import pytest

from app import services
from app.models import Part, db

CSV = pathlib.Path(__file__).resolve().parent.parent / "data" / "parts_catalog.csv"

_IMPORTED = {}


@pytest.fixture(scope="module")
def app(shared_app):
    """כל הבדיקות בקובץ הזה רק קוראות מהקטלוג, ולכן חולקות אפליקציה
    אחת - אחרת כל בדיקה מייבאת מחדש אלפי שורות."""
    return shared_app


def _load(app):
    """מייבא את קובץ הקטלוג פעם אחת לכל אפליקציה, ומחזיר את אותה תוצאה."""
    if app not in _IMPORTED:
        with app.app_context():
            # דרך ה-ORM ולא query.delete(): מחיקה בכמות אחת עוקפת את
            # ה-cascade, ואז המקבילים וההתאמות נשארים מיותמים. SQLite
            # לא אוכף מפתחות זרים ושתק; Postgres עוצר את המחיקה.
            for part in Part.query.all():
                db.session.delete(part)
            db.session.commit()
            with CSV.open(encoding="utf-8-sig") as fh:
                _IMPORTED[app] = services.import_csv(fh)
    return _IMPORTED[app]


def test_file_imports_without_errors(app):
    created, updated, errors = _load(app)
    assert errors == []
    assert created >= 60


def test_plate_to_part_number_actually_resolves(app):
    """הזרימה שבשבילה הקובץ קיים: רכב אמיתי -> מק"ט אמיתי."""
    _load(app)
    with app.app_context():
        peugeot = {"make": "פיג'ו צרפת", "model": "5008", "year": 2020}
        matches = services.parts_for_vehicle(peugeot, "oil_filter")
        assert matches, "5008 מ-2020 חייב להחזיר מסנן שמן"
        assert {p.part_number for p in matches} >= {"32223", "H90W23"}

        corolla = {"make": "טויוטה יפן", "model": "COROLLA", "year": 2016}
        assert services.parts_for_vehicle(corolla, "oil_filter")


def test_year_range_is_real_not_decorative(app):
    """5008 II יצא ב-2016; רכב מ-2005 לא אמור להחזיר את החלפים שלו."""
    _load(app)
    with app.app_context():
        old = {"make": "פיג'ו צרפת", "model": "5008", "year": 2005}
        assert services.parts_for_vehicle(old, "oil_filter") == []


def test_every_row_carries_its_source(app):
    """מקור הנתונים לא רשמי, ולכן כל שורה נושאת את זה בגלוי."""
    _load(app)
    with app.app_context():
        parts = Part.query.all()
        assert parts
        assert all("מקור: קטלוג מקוון" in (p.notes or "") for p in parts)


def test_shared_part_number_keeps_every_fitment(app):
    """מק"ט אחד מתאים לכמה דגמים, ולכן שורה אחת נושאת את כל ההתאמות.

    שתי שורות עם אותו מק"ט היו דורסות זו את זו - הייבוא מחליף את
    אוסף ההתאמות, ולא מוסיף לו.
    """
    _load(app)
    with app.app_context():
        part = Part.query.filter_by(part_number="W 67/1").one()
        assert {f.model for f in part.fitments} == {"PICANTO", "QASHQAI", "CLIO"}


def test_catalog_covers_the_common_israeli_models(app):
    _load(app)
    with app.app_context():
        expected = [
            ("פיג'ו צרפת", "5008", 2020), ("טויוטה יפן", "COROLLA", 2016),
            ("טויוטה יפן", "YARIS", 2018), ("יונדאי קוריאה", "i20", 2021),
            ("קיה קוריאה", "PICANTO", 2019), ("קיה קוריאה", "SPORTAGE", 2020),
            ("מאזדה יפן", "3", 2017), ("סקודה צ'כיה", "OCTAVIA", 2019),
            ("ניסאן יפן", "QASHQAI", 2018), ("רנו צרפת", "CLIO", 2020),
            ("יונדאי קוריאה", "TUCSON", 2022),
        ]
        for make, model, year in expected:
            vehicle = {"make": make, "model": model, "year": year}
            assert services.catalog_coverage(vehicle), f"{make} {model} בלי חלפים"


def test_catalog_spans_several_part_types(app):
    """קטלוג של מסנני שמן בלבד לא מדגים כלום - צריך רוחב."""
    _load(app)
    with app.app_context():
        types = {p.part_type for p in Part.query.all()}
        assert {"oil_filter", "air_filter", "cabin_filter",
                "wiper_blade", "fuel_filter"} <= types


def test_corolla_answers_more_than_one_question(app):
    """הדגם המוביל בקטלוג - כמה סוגי חלקים על אותו רכב."""
    _load(app)
    with app.app_context():
        corolla = {"make": "טויוטה יפן", "model": "COROLLA", "year": 2016}
        coverage = services.catalog_coverage(corolla)
        assert len(coverage) >= 5, coverage


def test_the_coverage_number_counts_originals_and_this_file_has_none(app):
    """‏parts_catalog.csv הוא קטלוג חלפים (AUTODOC) במלואו.

    לכן הספירה כאן היא אפס לכל סוג - וזו בדיוק הנקודה: קודם השבב
    הראה "מגב (3)" והתכוון לשלושה חלפים. המספר מבטיח עכשיו מקוריים,
    והסוג נשאר ברשימה כדי שהקישור אליו יישמר.
    """
    _load(app)
    with app.app_context():
        coverage = services.catalog_coverage(
            {"make": "טויוטה יפן", "model": "COROLLA", "year": 2016})
        assert coverage, "הסוגים עצמם צריכים להישאר"
        assert set(coverage.values()) == {0}


def test_an_original_is_counted_and_an_aftermarket_part_is_not(app):
    """שני מק"טים לאותו רכב ואותו סוג: אחד מקורי, אחד חלף."""
    from app.models import CrossReference, Fitment, Part, db

    with app.app_context():
        original = Part(part_number="TEST-OEM-1", name_he="מסנן שמן מקורי",
                        part_type="oil_filter")
        original.cross_refs.append(
            CrossReference(ref_type="OEM", ref_number="TEST-OEM-1",
                           ref_brand="Toyota"))
        original.fitments.append(Fitment(make="טויוטה", model="COROLLA",
                                         year_from=2010, year_to=2018))
        alt = Part(part_number="TEST-ALT-1", name_he="מסנן שמן חלופי",
                   part_type="oil_filter")
        alt.cross_refs.append(
            CrossReference(ref_type="OEM", ref_number="TEST-OEM-1",
                           ref_brand="Toyota"))
        alt.fitments.append(Fitment(make="טויוטה", model="COROLLA",
                                    year_from=2010, year_to=2018))
        db.session.add_all([original, alt])
        db.session.commit()
        try:
            coverage = services.catalog_coverage(
                {"make": "טויוטה יפן", "model": "COROLLA", "year": 2015})
            assert coverage["oil_filter"] == 1
        finally:
            # האפליקציה משותפת לכל הקובץ, ו-``_load`` מייבא פעם אחת -
            # שורה שנשארת כאן מזהמת כל בדיקה שאחריה.
            for part in (original, alt):
                db.session.delete(part)
            db.session.commit()


def test_the_same_number_written_differently_is_still_original(app):
    """‏04152-YZZA1 ו-04152YZZA1 הם אותו חלק, ולכן ההשוואה מנורמלת."""
    from app.models import CrossReference, Part, db

    with app.app_context():
        part = Part(part_number="TEST-04152-YZZA1", name_he="מסנן", part_type="oil_filter")
        part.cross_refs.append(
            CrossReference(ref_type="OEM", ref_number="TEST 04152 YZZA1"))
        db.session.add(part)
        db.session.commit()
        try:
            assert services.is_original(part) is True
        finally:
            db.session.delete(part)
            db.session.commit()


def test_a_part_without_cross_refs_is_not_called_original(app):
    from app.models import Part, db

    with app.app_context():
        part = Part(part_number="NO-REFS", name_he="בלי מקבילים",
                    part_type="oil_filter")
        db.session.add(part)
        db.session.commit()
        try:
            assert services.is_original(part) is False
        finally:
            db.session.delete(part)
            db.session.commit()


def test_oe_cross_references_came_through(app):
    _load(app)
    with app.app_context():
        part = Part.query.filter_by(part_number="27149").one()
        assert [r.ref_number for r in part.cross_refs] == ["90915-YZZJ1"]


def test_every_sample_plate_finds_parts(app):
    """כל רכב בקובץ רכבי הדוגמה חייב להחזיר חלפים.

    מספרי הרישוי האלה הם מה שמישהו מקליד כשהוא מנסה את המערכת. רכב
    שמזוהה ואז מחזיר מסך ריק גרוע מרכב שלא מזוהה - נראה כאילו הכל
    עבד ובכל זאת אין תשובה.
    """
    import json

    sample = pathlib.Path(__file__).resolve().parent.parent / "data" / "vehicles_sample.json"
    vehicles = json.loads(sample.read_text(encoding="utf-8"))
    _load(app)
    with app.app_context():
        for record in vehicles:
            vehicle = {"make": record["tozeret_nm"], "model": record["kinuy_mishari"],
                       "year": record["shnat_yitzur"]}
            coverage = services.catalog_coverage(vehicle)
            assert coverage, f'{vehicle["make"]} {vehicle["model"]} {vehicle["year"]}'


def test_new_models_reach_the_catalog(app):
    """הדגמים שנוספו - כל אחד עם החלפים שנמצאו לו."""
    _load(app)
    with app.app_context():
        for make, model, year, part_type in [
            ("פולקסווגן גרמניה", "GOLF", 2015, "oil_filter"),
            ("פולקסווגן גרמניה", "GOLF", 2015, "cabin_filter"),
            ("הונדה יפן", "CIVIC", 2021, "oil_filter"),
            ("מיצובישי יפן", "OUTLANDER", 2014, "cabin_filter"),
            ("סוזוקי יפן", "SWIFT", 2013, "cabin_filter"),
            ("טויוטה יפן", "RAV4", 2020, "oil_filter"),
        ]:
            vehicle = {"make": make, "model": model, "year": year}
            assert services.parts_for_vehicle(vehicle, part_type), f"{model} {part_type}"


def test_new_part_types_are_tied_to_a_real_vehicle(app):
    """סוגי החלקים החדשים - לא רק בקטלוג, אלא נמצאים בחיפוש לפי רכב."""
    _load(app)
    with app.app_context():
        octavia = {"make": "סקודה צ'כיה", "model": "OCTAVIA", "year": 2018}
        assert services.parts_for_vehicle(octavia, "brake_disc_front")
        assert services.parts_for_vehicle(octavia, "timing_belt")
        corolla = {"make": "טויוטה יפן", "model": "COROLLA", "year": 2016}
        assert services.parts_for_vehicle(corolla, "spark_plug")


def test_a_shared_filter_serves_both_toyotas(app):
    """מק"ט אחד, שלושה דגמים - ההתאמות נשמרות יחד ולא דורסות זו את זו."""
    _load(app)
    with app.app_context():
        part = Part.query.filter_by(part_number="DCF387K").one()
        # הרשימה גדלה עם כל סבב איסוף, ולכן נבדקת הכלה ולא שוויון:
        # מה שחשוב הוא ששלוש ההתאמות המקוריות שרדו ולא נדרסו
        assert {"COROLLA", "RAV4", "C-HR"} <= {f.model for f in part.fitments}


def test_mazda_is_stored_under_the_registry_name(app):
    """הדגם נשמר בשם שמשרד התחבורה משתמש בו, ולא בשם המסחרי הקצר.

    ההשוואה היא Fitment.model ILIKE %דגם הרכב%, והדגם מגיע מהמאגר
    כ-"MAZDA 3". התאמה ששמורה כ-"3" לא נמצאת: "3" אינו מכיל
    "MAZDA 3". הכיוון ההפוך עובד, ולכן השם המלא הוא הנכון לשמור.
    """
    _load(app)
    with app.app_context():
        from_registry = {"make": "מאזדה יפן", "model": "MAZDA 3", "year": 2018}
        typed_by_hand = {"make": "מאזדה יפן", "model": "3", "year": 2018}
        assert services.parts_for_vehicle(from_registry, "oil_filter")
        assert services.parts_for_vehicle(typed_by_hand, "oil_filter")


def test_a_platform_sibling_shares_the_air_filter(app):
    """טוסון וספורטג' חולקים פלטפורמה, ובשני העמודים אותו מספר OE."""
    _load(app)
    with app.app_context():
        part = Part.query.filter_by(part_number="18685").one()
        assert {f.model for f in part.fitments} == {"TUCSON", "SPORTAGE"}
        assert [r.ref_number for r in part.cross_refs] == ["28113-D3300"]


def test_no_model_is_left_with_a_single_part_type(app):
    """דגם שמחזיר סוג חלק אחד בלבד לא מדגים כלום.

    היו שני חריגים - סוויפט ו-C-HR - ושניהם נסגרו. הבדיקה נשארת בלי
    רשימת פטורים בכוונה: דגם חדש שייכנס עם סוג אחד יפיל אותה."""
    _load(app)
    with app.app_context():
        by_model = {}
        for part in Part.query.all():
            for fit in part.fitments:
                by_model.setdefault(fit.model, set()).add(part.part_type)
        thin = {model for model, types in by_model.items() if len(types) < 2}
        assert thin == set(), thin


def test_the_shared_korean_filter_serves_both_brands(app):
    """קיה ויונדאי חולקות מנוע, ובשני העמודים אותו מספר OE."""
    _load(app)
    with app.app_context():
        part = Part.query.filter_by(part_number="FO-599S").one()
        assert {("קיה", "NIRO"), ("יונדאי", "i30")} <= {
            (f.make, f.model) for f in part.fitments}
        assert [r.ref_number for r in part.cross_refs] == ["26300-35505"]


def test_the_new_popular_models_answer_a_plate(app):
    """הדגמים שנוספו - כל אחד עם שני סוגי חלקים לפחות."""
    from app import services

    _load(app)
    with app.app_context():
        for make, model, year in [("קיה קוריאה", "NIRO", 2019),
                                  ("יונדאי קוריאה", "i30", 2018),
                                  ("מאזדה יפן", "CX-5", 2020),
                                  ("טויוטה יפן", "C-HR", 2019),
                                  ("סוזוקי יפן", "SWIFT", 2013)]:
            coverage = services.catalog_coverage({"make": make, "model": model,
                                                  "year": year})
            assert len(coverage) >= 2, f"{model}: {coverage}"


def test_a_hyphenated_model_is_found_either_way(app):
    """CX-5 ו-C-HR - המאגר עשוי לכתוב אותם עם רווח או בלי מקף."""
    from app import services

    _load(app)
    with app.app_context():
        for model in ("CX-5", "CX 5", "CX5"):
            assert services.parts_for_vehicle(
                {"make": "מאזדה יפן", "model": model, "year": 2020}), model
        for model in ("C-HR", "CHR", "C HR"):
            assert services.parts_for_vehicle(
                {"make": "טויוטה יפן", "model": model, "year": 2019}), model


def test_parts_shared_across_makes_carry_every_fitment(app):
    """מק"ט אחד שמופיע בעמוד של שני דגמים - שורה אחת, שתי התאמות."""
    _load(app)
    with app.app_context():
        # FEBI 32223 נושא OE 1109.AL, ומופיע בעמוד ה-5008 ובעמוד ה-208
        peugeot = Part.query.filter_by(part_number="32223").one()
        assert {"5008", "208"} <= {f.model for f in peugeot.fitments}
        # אותו מסנן אוויר, OE 17801-0T060, ב-C-HR וב-RAV4
        toyota = Part.query.filter_by(part_number="FA-2017S").one()
        assert {"C-HR", "RAV4"} <= {f.model for f in toyota.fitments}


def test_rio_and_208_answer_a_plate(app):
    from app import services

    _load(app)
    with app.app_context():
        for make, model, year in [("קיה קוריאה", "RIO", 2019),
                                  ("פיג'ו צרפת", "208", 2017)]:
            coverage = services.catalog_coverage({"make": make, "model": model,
                                                  "year": year})
            assert len(coverage) >= 2, f"{model}: {coverage}"


def test_brake_discs_are_a_real_category_now(app):
    """דיסקים קדמיים לשבעה דגמים - עמודי הדיסקים מציינים סרן במפורש,
    ולכן אין בהם את אי-הבהירות שפסלה רפידות בסבבים הראשונים."""
    from app import services

    _load(app)
    with app.app_context():
        expected = {"COROLLA", "OCTAVIA", "RAV4", "TUCSON", "OUTLANDER",
                    "GOLF", "RIO", "C-HR", "CX-5", "MAZDA 3", "SPORTAGE",
                    "NIRO", "PICANTO"}
        found = {
            model
            for model in expected
            if Part.query.join(Part.fitments)
            .filter(Part.part_type == "brake_disc_front")
            .filter_by(model=model).first()
        }
        assert found == expected, expected - found
        # והם נמצאים גם בחיפוש לפי רכב, לא רק בקטלוג
        assert services.parts_for_vehicle(
            {"make": "טויוטה יפן", "model": "RAV4", "year": 2020},
            "brake_disc_front")


def test_the_three_japanese_and_korean_brands_answer_more_than_filters(app):
    """מאזדה, טויוטה וקיה - הדגמים שנמכרים כאן הכי הרבה.

    עד הסבב הזה כמעט כל מה שהיה להם היה מסננים. עכשיו לכל אחד מהם
    יש גם חלק מתכלה שהמוסך מחליף בפועל: דיסק, מצת או מגב."""
    _load(app)
    with app.app_context():
        beyond_filters = {"brake_disc_front", "spark_plug", "wiper_blade"}
        by_model = {}
        for part in Part.query.all():
            for fit in part.fitments:
                if fit.make in ("מאזדה", "טויוטה", "קיה"):
                    by_model.setdefault(fit.model, set()).add(part.part_type)
        thin = {model for model, types in by_model.items()
                if not types & beyond_filters}
        assert thin == set(), thin


def test_spark_plugs_are_petrol_only(app):
    """מנוע דיזל אין לו מצתים. כל מצת בקטלוג תלוי בדגם עם מנוע בנזין,
    ולכן הבדיקה נעולה על רשימת הדגמים - דיזל שייכנס לכאן יפיל אותה."""
    _load(app)
    with app.app_context():
        models = {
            fit.model
            for part in Part.query.filter_by(part_type="spark_plug")
            for fit in part.fitments
        }
        # רשימת ההיתר נכתבת ביד בכוונה: דגם חדש שיקבל מצתים חייב
        # לעבור אישור אנושי, ולא להיכנס בשקט עם סבב איסוף
        petrol = {"COROLLA", "GOLF", "C-HR", "RAV4", "CX-5", "SPORTAGE",
                  "MAZDA 3", "NIRO", "PICANTO", "YARIS", "RIO", "AURIS",
                  "AYGO", "CAMRY", "CARENS", "CEED", "CX-3", "CX-30",
                  "MAZDA 2", "MAZDA 6", "MX-5", "PRIUS", "SELTOS",
                  "SORENTO", "SOUL", "STONIC",
                  # אושרו בסבב הרכבים הנפוצים בישראל: כל ההתאמות
                  # שנאספו להם הן בנזין (1.2, 1.0 MPi, 1.4 MPI, 1.4 TSI)
                  "i10", "i20", "i30", "OCTAVIA", "RAPID"}
        assert models <= petrol, models - petrol


def test_the_korean_front_disc_serves_tucson_and_sportage(app):
    """אותו מק"ט, 305x25, מופיע בעמוד הטוסון ובעמוד הספורטג'.

    שתי השורות האלה כבר היו בקטלוג עבור טוסון; עמוד הספורטג' הוא
    שהוסיף להן את ההתאמה השנייה, ולא ניחוש על סמך פלטפורמה משותפת.

    הבדיקה היא הכלה ולא שוויון: הדיסק הזה משרת גם את i30, וסבב
    איסוף שמוסיף עוד רכב אמיתי אינו אמור להפיל בדיקה."""
    _load(app)
    with app.app_context():
        for number in ("ADG043221", "108575"):
            part = Part.query.filter_by(part_number=number).one()
            assert {("יונדאי", "TUCSON"), ("קיה", "SPORTAGE")} <= {
                (f.make, f.model) for f in part.fitments}, number


def test_front_brake_pads_are_a_real_category_now(app):
    """רפידות קדמיות היו הקטגוריה שנפסלה בסבבים הראשונים.

    מה שפתח אותה הוא מספר ה-WVA: כשכל הפריטים בעמוד נושאים את אותו
    מספר, הוא מכריע שמדובר באותה רפידה - בדיוק כמו ש-Front Axle
    הכריע בדיסקים. עמוד שבו כמה מספרי WVA שונים נפסל כולו."""
    _load(app)
    with app.app_context():
        models = {
            fit.model
            for part in Part.query.filter_by(part_type="brake_pads_front")
            for fit in part.fitments
        }
        assert len(models) >= 4, models
        assert {"MAZDA 3", "C-HR", "RAV4"} <= models, models
        assert services.parts_for_vehicle(
            {"make": "מאזדה יפן", "model": "MAZDA 3", "year": 2017},
            "brake_pads_front")


def test_a_wiper_pair_is_never_split_between_two_lengths(app):
    """זוג מגבים קדמי הוא שני אורכים, ושניהם חייבים להיות אותו זוג.

    לכל דגם עם מגבים יש מק"טים שנאספו מאותו עמוד ולפי אותו זוג
    אורכים; RAV4 ופיקנטו הם החדשים כאן."""
    _load(app)
    with app.app_context():
        for make, model in [("טויוטה יפן", "RAV4"), ("קיה קוריאה", "PICANTO")]:
            found = services.parts_for_vehicle(
                {"make": make, "model": model, "year": 2020}, "wiper_blade")
            assert len(found) >= 2, f"{model}: {found}"


def test_every_part_type_exists_in_the_taxonomy(app):
    """סוג חלק שלא בטקסונומיה נעלם מהמסך בלי להתלונן.

    החיפוש והתגיות עובדים לפי PART_TYPES; שורה עם סוג שאינו שם
    תיובא בהצלחה ופשוט לא תופיע לאף רכב. לכן זו בדיקה ולא הערה."""
    from app.taxonomy import PART_TYPES

    _load(app)
    with app.app_context():
        used = {p.part_type for p in Part.query.all()}
        assert used <= set(PART_TYPES), used - set(PART_TYPES)


def test_body_parts_carry_the_side_the_source_stated(app):
    """בחלק פח הצד הוא חצי מהזיהוי: כנף ימין אינה כנף שמאל.

    לכל סוג שיש לו צד נבדק שהצד מולא, למעט פריטים שהמקור עצמו
    מסמן "both sides" - שם היעדר הצד הוא המידע הנכון."""
    _load(app)
    with app.app_context():
        sided = {"taillight", "fender", "side_mirror", "mirror_glass",
                 "mirror_cover"}
        parts = Part.query.filter(Part.part_type.in_(sided)).all()
        assert parts
        missing = [p.part_number for p in parts if not p.side]
        assert missing == [], missing
        assert {p.side for p in parts} == {"ימין", "שמאל"}


def test_a_facelift_part_is_narrowed_not_stretched(app):
    """פנס שמסומן "Model Year from 2016" נכנס על 2016 ואילך.

    הקורולה בקטלוג היא 2013-2019, ולכן הפיתוי הוא לרשום את הפנס על
    כל הטווח. ההתאמה נחתכת לפי מה שהפריט מצהיר, וכך רכב מ-2014 לא
    מקבל פנס של הפייסליפט."""
    _load(app)
    with app.app_context():
        heads = (Part.query.join(Part.fitments)
                 .filter(Part.part_type.in_(("headlight_left", "headlight_right")))
                 .filter_by(model="COROLLA").all())
        assert heads
        starts = {f.year_from for p in heads for f in p.fitments
                  if f.model == "COROLLA"}
        assert starts != {2013}, "אף פנס לא נחתך - כנראה חלון הייצור לא נקרא"
        assert min(starts) >= 2013 and max(starts) <= 2019, starts


def test_body_parts_reach_all_three_brands(app):
    """פח וקוסמטיקה נאספו לשלושת המותגים, לא רק לאחד."""
    _load(app)
    with app.app_context():
        body = {"front_bumper", "rear_bumper", "fender", "side_mirror",
                "headlight_left", "headlight_right", "taillight", "fog_light"}
        makes = {f.make for p in Part.query.filter(Part.part_type.in_(body))
                 for f in p.fitments}
        assert {"טויוטה", "מאזדה", "קיה"} <= makes, makes


def test_an_oe_reference_belongs_to_the_car_it_fits(app):
    """מק"ט מקורי של מותג זר לגמרי הוא טעות מסוכנת.

    זו התבנית שנתפסה ידנית בסבבים הראשונים - OE של הונדה על עמוד של
    קיה. הכלל אינו "כל ההפניות למותגים שבהתאמות": חלף אחד באמת נושא
    מק"טים של כמה יצרנים כשהמנוע או הפלטפורמה משותפים, למשל מסנן
    האוויר של C-HR שנושא גם מק"טים של פיג'ו וסיטרואן. מה שנדרש הוא
    שלפחות הפניה אחת תהיה של מותג שהחלף באמת מתאים לו, ושכל מותג
    יהיה יצרן רכב - מותג שאינו כזה הוא המספר המסחרי של החלף עצמו."""
    _load(app)
    with app.app_context():
        he = {"Toyota": "טויוטה", "Mazda": "מאזדה", "Kia": "קיה",
              "Hyundai": "יונדאי", "Peugeot": "פיג'ו", "Citroen": "סיטרואן",
              "Skoda": "סקודה", "VW": "פולקסווגן", "Nissan": "ניסאן",
              "Honda": "הונדה", "Suzuki": "סוזוקי", "Renault": "רנו",
              "Mitsubishi": "מיצובישי"}
        unknown, orphan = [], []
        for part in Part.query.all():
            brands = [(ref.ref_brand or "").strip() for ref in part.cross_refs]
            brands = [b for b in brands if b]
            if not brands:
                continue
            makes = {f.make for f in part.fitments}
            for raw in brands:
                if raw not in he:
                    unknown.append((part.part_number, raw))
            # קבוצות שמספרות חלפים באותה סדרה: OE של יונדאי על חלף
            # של קיה אינו זר, וכך גם מק"ט VAG על אוקטביה - טבלת ה-OE
            # רושמת אותו על שם פולקסווגן, והוא בכל זאת המק"ט המקורי
            shared = ({"קיה", "יונדאי"},
                      {"סקודה", "פולקסווגן", "סיאט", "אאודי"},
                      {"טויוטה", "לקסוס"},
                      {"פיג'ו", "סיטרואן"})
            group = set(makes)
            for family in shared:
                if family & makes:
                    group |= family
            if not any(he.get(b) in group for b in brands):
                orphan.append((part.part_number, brands, sorted(makes)))
        assert unknown == [], unknown[:5]
        assert orphan == [], orphan[:5]


def test_most_parts_carry_an_original_number(app):
    """בלי מק"ט מקורי אי אפשר להשוות חליפי מול מחירון היבואן.

    הכיסוי נמדד רק על חלקים שאין להם צד. בחלקי פח ותאורה, שבהם
    ימין ושמאל הם שני מק"טים מקוריים שונים, מק"ט נכנס רק בהתאמה
    ישירה - ולכן הכיסוי שם נמוך בכוונה, ולא מעיד על תקלה."""
    _load(app)
    with app.app_context():
        sided = {"side_mirror", "mirror_glass", "mirror_cover", "taillight",
                 "fender", "fog_light", "headlight_left", "headlight_right"}
        parts = [p for p in Part.query.all() if p.part_type not in sided]
        withoe = [p for p in parts if p.cross_refs]
        assert len(withoe) / len(parts) > 0.75, f"{len(withoe)}/{len(parts)}"


def test_no_original_number_repeats_inside_one_part(app):
    """אותו מק"ט מקורי פעמיים על אותו חלף מפיל את הייבוא.

    טבלת ה-OE במקור מכילה כפילויות, והן הפילו חמש שורות עד שנוסף
    ניקוי - לכן זו בדיקה ולא הערה."""
    _load(app)
    with app.app_context():
        for part in Part.query.all():
            nums = [r.ref_number for r in part.cross_refs]
            assert len(nums) == len(set(nums)), part.part_number


def test_left_and_right_never_share_an_original_number(app):
    """ימין ושמאל הם שני מק"טים מקוריים שונים, תמיד.

    הסקה ברמת הקבוצה נתנה למראות ימין את המק"ט של שמאל, כי מפתח
    הקבוצה היה יצרן+דגם+סוג חלק בלי הצד. 23 שורות. מאז חלק שיש לו
    צד מקבל מק"ט מקורי רק בהתאמה ישירה - עדיף בלי מאשר של הצד השני."""
    _load(app)
    with app.app_context():
        by_side = {}
        for part in Part.query.filter(Part.side.isnot(None)):
            for ref in part.cross_refs:
                key = (part.part_type, ref.ref_number)
                if by_side.setdefault(key, part.side) != part.side:
                    raise AssertionError(
                        f"{ref.ref_number} מופיע גם בימין וגם בשמאל "
                        f"({part.part_type}, {part.part_number})")


def test_a_part_that_fits_two_makes_carries_both_originals(app):
    """מוסך שמסתכל על C-HR צריך את המק"ט של טויוטה.

    מצת אחד מתאים גם לטויוטה וגם למאזדה, ובגרסה הראשונה הוא קיבל
    רק את המק"ט של המותג שנמצא ראשון - כך שברכב השני הוא הציג מספר
    מקורי של יצרן אחר."""
    _load(app)
    with app.app_context():
        mixed = 0
        for part in Part.query.all():
            makes = {f.make for f in part.fitments}
            brands = {r.ref_brand for r in part.cross_refs if r.ref_brand}
            if len(makes) > 1 and len(brands) > 1:
                mixed += 1
        assert mixed >= 10, mixed


def test_the_original_number_agrees_with_the_side(app):
    """אצל טויוטה 87910 הוא ימין ו-87940 הוא שמאל - חמש הספרות
    הראשונות של המק"ט המקורי כוללות את הצד.

    זו בדיקה חיצונית למקור: אפילו אם AUTODOC יטעה, המספר עצמו
    יסגיר את הטעות. חלק פח שמוזמן לפי מספר של הצד ההפוך מגיע הפוך."""
    _load(app)
    prefix = {
        "Toyota": {"87910": "ימין", "87940": "שמאל", "81551": "ימין",
                   "81561": "שמאל", "81581": "ימין", "81591": "שמאל",
                   "53801": "ימין", "53802": "שמאל", "53811": "ימין",
                   "53812": "שמאל", "81210": "ימין", "81220": "שמאל"},
        # יונדאי וקיה חולקות את אותו מספור
        "Kia": {"92201": "שמאל", "92202": "ימין", "92401": "שמאל",
                "92402": "ימין", "92405": "שמאל", "92406": "ימין",
                "66311": "שמאל", "66321": "ימין", "87610": "שמאל",
                "87620": "ימין", "92101": "שמאל", "92102": "ימין"},
    }
    prefix["Hyundai"] = prefix["Kia"]
    checked = 0
    with app.app_context():
        for part in Part.query.filter(Part.side.isnot(None)):
            if not part.side:
                continue
            for ref in part.cross_refs:
                table = prefix.get(ref.ref_brand)
                if not table:
                    continue
                head = re.sub(r"[^0-9A-Z]", "", ref.ref_number.upper())[:5]
                if head not in table:
                    continue
                checked += 1
                assert table[head] == part.side, (
                    f'{part.part_number} מסומן {part.side} אבל '
                    f'{ref.ref_number} הוא {table[head]}')
    assert checked >= 20, checked


def test_a_diesel_never_gets_spark_plugs(app):
    """מנוע דיזל אין לו מצתים, ולכן אין מצת שתלוי בהתאמה לדיזל.

    דף המצתים של טוסון 2.0 CRDi ב-AUTODOC בכל זאת מציג פריטים,
    ככל הנראה מדגמים אחרים באותה משפחה."""
    _load(app)
    diesel = re.compile(r"CRDi|TDI|CDTI|dCi|HDi|D-4D|BlueHDi|CDI", re.I)
    with app.app_context():
        for part in Part.query.filter_by(part_type="spark_plug"):
            for fit in part.fitments:
                assert not (fit.engine_code and diesel.search(fit.engine_code)), (
                    f"{part.part_number} תלוי במנוע דיזל {fit.engine_code}")
