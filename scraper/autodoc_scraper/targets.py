"""מטרה -> כתובת באתר.

הקטלוג שלנו מדבר במפתחות של app/taxonomy.py ("oil_filter") ובשמות
יצרן בעברית ("טויוטה"). האתר מדבר בכתובות לועזיות. כאן מתרגמים.

המיפוי הוא נקודת פתיחה, לא אמת מוחלטת: מבנה הכתובות של חנות מקוונת
משתנה, וגם מזהה קטגוריה מספרי עשוי להידרש. כתובת שגויה לא שוברת כלום -
היא מחזירה אפס תוצאות, והיומן במסך אומר בדיוק את זה. שלוש דרכי תיקון,
לפי סדר המאמץ:

  1. להדביק כתובת מדויקת במסך - עוקף את המיפוי לגמרי, למטרה אחת.
  2. AUTODOC_CATEGORIES - JSON שדורס ערכים כאן, בלי פריסה מחדש.
  3. לתקן את הטבלה הזאת.
"""
import json
import os
import re

BASE_URL = os.environ.get("AUTODOC_BASE_URL", "https://www.autodoc.co.il").rstrip("/")

# {base} - כתובת האתר, {category} - מקטע הקטגוריה, {make}/{model} - הרכב
URL_TEMPLATE = os.environ.get(
    "AUTODOC_URL_TEMPLATE", "{base}/car-parts/{category}/{make}/{model}"
)

# מפתח סוג החלק (app/taxonomy.py) -> מקטע הקטגוריה בכתובת
CATEGORIES = {
    "oil_filter": "oil-filter",
    "air_filter": "air-filter",
    "cabin_filter": "pollen-filter",
    "fuel_filter": "fuel-filter",
    "brake_pads_front": "brake-pads",
    "brake_pads_rear": "brake-pads",
    "brake_disc_front": "brake-disc",
    "brake_disc_rear": "brake-disc",
    "brake_caliper": "brake-caliper",
    "wiper_blade": "wiper-blades",
    "spark_plug": "spark-plug",
    "ignition_coil": "ignition-coil",
    "timing_belt": "timing-belt",
    "serpentine_belt": "v-ribbed-belt",
    "water_pump": "water-pump",
    "thermostat": "thermostat",
    "radiator": "radiator",
    "radiator_fan": "radiator-fan",
    "alternator": "alternator",
    "starter": "starter-motor",
    "battery": "battery",
    "shock_absorber_front": "shock-absorber",
    "shock_absorber_rear": "shock-absorber",
    "coil_spring": "coil-spring",
    "control_arm": "control-arm",
    "ball_joint": "ball-joint",
    "stabilizer_link": "anti-roll-bar-link",
    "wheel_bearing": "wheel-bearing",
    "engine_mount": "engine-mount",
    "ac_compressor": "air-conditioning-compressor",
    "oxygen_sensor": "lambda-sensor",
    "abs_sensor": "abs-sensor",
    "fuel_pump": "fuel-pump",
    "catalytic_converter": "catalytic-converter",
    "headlight_right": "headlights",
    "headlight_left": "headlights",
    "taillight": "tail-light",
    "fog_light": "fog-light",
    "side_mirror": "wing-mirror",
    "front_bumper": "bumper",
    "rear_bumper": "bumper",
    "fender": "wing",
    "windshield": "windscreen",
}

# שם היצרן בעברית -> הכתיב שבכתובת. אותה טבלה כמו ב-app/parts_discovery.py,
# משוכפלת בכוונה: הגריד רץ כתהליך נפרד ואינו מייבא את אפליקציית Flask.
MAKE_SLUGS = {
    "טויוטה": "toyota", "לקסוס": "lexus", "הונדה": "honda", "מאזדה": "mazda",
    "ניסאן": "nissan", "מיצובישי": "mitsubishi", "סוזוקי": "suzuki",
    "סובארו": "subaru", "יונדאי": "hyundai", "קיה": "kia", "פיג'ו": "peugeot",
    "סיטרואן": "citroen", "רנו": "renault", "סקודה": "skoda", "סיאט": "seat",
    "פולקסווגן": "volkswagen", "אאודי": "audi", "אודי": "audi", "פורד": "ford",
    "אופל": "opel", "שברולט": "chevrolet", "מרצדס": "mercedes-benz",
    "וולוו": "volvo", "ב.מ.וו": "bmw", "במוו": "bmw", "מיני": "mini",
    "דאצ'יה": "dacia", "פיאט": "fiat", "טסלה": "tesla", "צ'רי": "chery",
    "ג'ילי": "geely", "אם.ג'י": "mg", "ג'יפ": "jeep",
}


def _overrides():
    """דריסות מהסביבה. JSON פגום לא מפיל את הגריד - הוא פשוט מתעלם."""
    raw = os.environ.get("AUTODOC_CATEGORIES", "").strip()
    if not raw:
        return {}
    try:
        loaded = json.loads(raw)
    except ValueError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def category_of(part_type):
    """מקטע הקטגוריה של סוג חלק, או None אם אין מיפוי."""
    return _overrides().get(part_type) or CATEGORIES.get(part_type)


def slugify(value):
    """שם רכב כפי שהוא מופיע בכתובת: אותיות קטנות, מקפים במקום רווחים."""
    text = MAKE_SLUGS.get((value or "").strip(), value or "")
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower())
    return text.strip("-")


def listing_url(make, model, part_type, base_url=None):
    """הכתובת שממנה מתחילים. None כשאין מיפוי לסוג החלק.

    בלי מיפוי לא בונים כתובת מנחשת: מטרה בלי כתובת נרשמת ביומן כמדולגת,
    וזה מידע - להבדיל מבקשה שיוצאת לכתובת מומצאת ומחזירה 404.
    """
    category = category_of(part_type)
    make_slug, model_slug = slugify(make), slugify(model)
    if not (category and make_slug and model_slug):
        return None
    return URL_TEMPLATE.format(
        base=(base_url or BASE_URL).rstrip("/"),
        category=category,
        make=make_slug,
        model=model_slug,
    )
