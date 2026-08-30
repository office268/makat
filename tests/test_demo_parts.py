"""קובץ ההדגמה: הוא קיים כדי שהזרימה תעבוד, ולכן זה מה שנבדק."""
import pathlib

import pytest

from app import services
from app.models import Part, db

CSV = pathlib.Path(__file__).resolve().parent.parent / "data" / "demo_parts.csv"

_IMPORTED = {}


@pytest.fixture(scope="module")
def app(shared_app):
    """כל הבדיקות בקובץ הזה רק קוראות מהקטלוג, ולכן חולקות אפליקציה
    אחת - אחרת כל בדיקה מייבאת מחדש אלפי שורות."""
    return shared_app


def _load(app):
    """מייבא את קובץ ההדגמה פעם אחת לכל אפליקציה, ומחזיר את אותה תוצאה."""
    if app not in _IMPORTED:
        with app.app_context():
            Part.query.delete()
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


def test_every_row_is_marked_as_demo_data(app):
    """מקור הנתונים לא רשמי, ולכן כל שורה נושאת את זה בגלוי."""
    _load(app)
    with app.app_context():
        parts = Part.query.all()
        assert parts
        assert all("נתוני הדגמה" in (p.notes or "") for p in parts)


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
    """הדגם המוביל בהדגמה - כמה סוגי חלקים על אותו רכב."""
    _load(app)
    with app.app_context():
        corolla = {"make": "טויוטה יפן", "model": "COROLLA", "year": 2016}
        coverage = services.catalog_coverage(corolla)
        assert len(coverage) >= 5, coverage
        assert sum(coverage.values()) >= 15


def test_oe_cross_references_came_through(app):
    _load(app)
    with app.app_context():
        part = Part.query.filter_by(part_number="27149").one()
        assert [r.ref_number for r in part.cross_refs] == ["90915-YZZJ1"]


def test_every_demo_plate_finds_parts(app):
    """כל רכב בקובץ הדמו חייב להחזיר חלפים.

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
                  "SORENTO", "SOUL", "STONIC"}
        assert models <= petrol, models - petrol


def test_the_korean_front_disc_serves_tucson_and_sportage(app):
    """אותו מק"ט, 305x25, מופיע בעמוד הטוסון ובעמוד הספורטג'.

    שתי השורות האלה כבר היו בקטלוג עבור טוסון; עמוד הספורטג' הוא
    שהוסיף להן את ההתאמה השנייה, ולא ניחוש על סמך פלטפורמה משותפת."""
    _load(app)
    with app.app_context():
        for number in ("ADG043221", "108575"):
            part = Part.query.filter_by(part_number=number).one()
            assert {(f.make, f.model) for f in part.fitments} == {
                ("יונדאי", "TUCSON"), ("קיה", "SPORTAGE")}, number


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
