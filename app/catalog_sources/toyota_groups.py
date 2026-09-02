"""קיצור דרך: מסוג חלק ישר לתרשים שבו הוא יושב, בקטלוג טויוטה.

המסע הרגיל בקטלוג הוא עיוור: כל עמוד נקרא על ידי המודל, שמחזיר לאן
ללכת הלאה. זה עובד, אבל הוא משלם בקשת רשת וקריאת מודל על כל צעד -
ובקטלוג טויוטה שני הצעדים האמצעיים ידועים מראש, כי מספרי הקבוצות שם
הם תקן: ``4705`` הוא מלגז בלם קדמי באותה מידה ב-RAV4 2015 ובהיילקס
2025, בשני שווקים שונים. זה נבדק מול שני הרכבים האלה בפועל.

לכן, ברגע שהעמוד הראשון זיהה את הרכב, אפשר לבנות את כתובת התרשים
ישירות במקום לשאול את המודל פעמיים לאן ללכת. המסע מתקצר מארבעה צעדים
לשניים: חיפוש שלדה, ואז התרשים עצמו.

זה קיצור ולא החלפה: כשהתבנית אינה מזוהה, כשהיצרן אינו טויוטה, או
כשלסוג החלק אין קבוצה ידועה - הפונקציה מחזירה ``None`` והמסע הרגיל
ממשיך כרגיל. אותו דבר כשהקיצור הגיע לתרשים ולא היו בו מק"טים: הקוד
הקורא נופל בחזרה למסע המודל, כי בדגם מסוים הקבוצה עשויה להיות אחרת
(היברידי, שוק אחר) והמפה כאן היא הניחוש הטוב, לא חוזה.
"""
import re

# ‏(קטגוריה, קבוצה) לכל סוג חלק. הקטגוריה היא אחת מארבע החטיבות של
# הקטלוג - מנוע, שסי, מרכב, חשמל - והקבוצה היא התרשים בתוכה.
#
# המפה נבנתה מתוך רשימות הקבוצות המלאות של רכב טויוטה אמיתי (כל 143
# התרשימים בארבע הקטגוריות), ולא מהשערה על שמות.
GROUPS = {
    # בלמים
    "brake_pads_front": (2, "4705"),   # FRONT DISC BRAKE CALIPER & DUST COVER
    "brake_pads_rear": (2, "4707"),    # REAR DISC BRAKE CALIPER & DUST COVER
    "brake_caliper": (2, "4705"),
    "brake_disc_front": (2, "4303"),   # FRONT AXLE HUB - שם יושב הדיסק
    "brake_disc_rear": (2, "4102"),    # REAR AXLE SHAFT & HUB
    # מנוע וסינון
    "oil_filter": (1, "1502"),         # OIL FILTER
    "air_filter": (1, "1703"),         # AIR CLEANER
    "fuel_filter": (3, "7751"),        # FUEL TANK & TUBE - המסנן בתוך המיכל
    "cabin_filter": (4, "8714"),       # HEATING & AIR CONDITIONING - COOLER UNIT
    "timing_belt": (1, "1106"),        # TIMING GEAR COVER & REAR END PLATE
    "serpentine_belt": (1, "1605"),    # V-BELT
    "engine_mount": (1, "1107"),       # MOUNTING
    # קירור
    "water_pump": (1, "1601"),         # WATER PUMP
    "thermostat": (1, "1603"),         # RADIATOR & WATER OUTLET
    "radiator": (1, "1603"),
    "radiator_fan": (1, "1603"),
    # הצתה וחשמל
    "spark_plug": (1, "1901"),         # IGNITION COIL & SPARK PLUG
    "ignition_coil": (1, "1901"),
    "alternator": (1, "1903"),         # ALTERNATOR
    "starter": (1, "1904"),            # STARTER
    "battery": (4, "8201"),            # BATTERY & BATTERY CABLE
    # מתלים והיגוי
    "shock_absorber_front": (2, "4803"),  # FRONT SPRING & SHOCK ABSORBER
    "shock_absorber_rear": (2, "4804"),   # REAR SPRING & SHOCK ABSORBER
    "coil_spring": (2, "4803"),
    "stabilizer_link": (2, "4803"),
    "control_arm": (2, "4802"),        # FRONT AXLE ARM & STEERING KNUCKLE
    "ball_joint": (2, "4802"),
    "wheel_bearing": (2, "4303"),      # FRONT AXLE HUB
    # תאורה
    "headlight_right": (4, "8101"),    # HEADLAMP
    "headlight_left": (4, "8101"),
    "headlight_lens": (4, "8101"),
    "daytime_running_light": (4, "8101"),
    "fog_light": (4, "8102"),          # FOG LAMP
    "taillight": (4, "8111"),          # REAR COMBINATION LAMP
    "rear_fog_light": (4, "8111"),
    "third_brake_light": (4, "8117"),  # CENTER STOP LAMP
    "number_plate_light": (4, "8113"), # REAR LICENSE PLATE LAMP
    # פחחות
    "front_bumper": (3, "5252"),       # FRONT BUMPER & BUMPER STAY
    "bumper_trim": (3, "5252"),
    "bumper_bracket": (3, "5252"),
    "rear_bumper": (3, "5253"),        # REAR BUMPER & BUMPER STAY
    "bumper_grille": (3, "5351"),      # RADIATOR GRILLE
    "fender": (3, "5353"),             # HOOD & FRONT FENDER
    "windshield": (3, "5553"),         # COWL PANEL & WINDSHIELD GLASS
    # מראות, מגבים, מיזוג
    "side_mirror": (4, "8701"),        # MIRROR
    "mirror_glass": (4, "8701"),
    "mirror_cover": (4, "8701"),
    "wiper_blade": (4, "8501"),        # WINDSHIELD WIPER
    "ac_compressor": (4, "8719"),      # HEATING & AIR CONDITIONING - COMPRESSOR
    # חיישנים, דלק, פליטה
    "abs_sensor": (4, "8414"),         # ABS & VSC
    "oxygen_sensor": (1, "1702"),      # EXHAUST PIPE
    "catalytic_converter": (1, "1702"),
    "fuel_pump": (3, "7751"),          # FUEL TANK & TUBE
}

# ‏.../catalog/toyota/vehicle/{שוק}/{שנה}/{דגם}/{קוד דגם}/category/{n}/vin/{שלדה}
# ארבעת המקטעים שבין ``vehicle`` ל-``category`` הם זהות הרכב, והם
# נשמרים כמו שהם. רק הקטגוריה ומה שאחריה נבנים מחדש.
VEHICLE_URL = re.compile(
    r"^(?P<prefix>https?://[^/]+/[^/]+/catalog/toyota)/vehicle/"
    r"(?P<vehicle>[^/]+/[^/]+/[^/]+/[^/]+)/category/[^/]+"
    r"(?:/vin/(?P<vin>[^/?#]+))?/?(?:[?#].*)?$",
    re.IGNORECASE,
)


def group_for(part_type):
    """‏(קטגוריה, קבוצה) לסוג החלק, או ``None`` כשאין קבוצה ידועה."""
    return GROUPS.get(part_type)


def diagram_url(url, part_type):
    """כתובת התרשים לסוג החלק, מתוך כתובת עמוד רכב. ``None`` כשאין.

    ``None`` מוחזר בכל אחד מהמקרים שבהם הקיצור אינו בטוח: הכתובת אינה
    עמוד רכב של קטלוג טויוטה, או שלסוג החלק אין קבוצה במפה. שני אלה
    אינם שגיאה - הם הסימן שצריך להמשיך במסע הרגיל.
    """
    if not url or not part_type:
        return None
    group = GROUPS.get(part_type)
    if not group:
        return None
    match = VEHICLE_URL.match(url.strip())
    if not match:
        return None
    category, diagram = group
    built = (
        f"{match.group('prefix')}/diagram/{match.group('vehicle')}"
        f"/category/{category}/diagram/{diagram}"
    )
    vin = match.group("vin")
    if vin:
        built += f"/vin/{vin}"
    return built
