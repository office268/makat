"""טעינת קטלוג דמו - מק"טים, יצרנים, התאמות לרכב ומק"טים מקבילים.

הקטלוג נבנה סביב עשרת הרכבים שבקובץ הדוגמאות, כך שכל חיפוש בדמו
(מספר רישוי + סוג חלק) מחזיר תוצאה אמיתית.
"""
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.auth_models import Organization, User  # noqa: E402
from app.models import (  # noqa: E402
    CrossReference,
    Fitment,
    OrgPart,
    PartSupplier,
    Supplier,
    db,
)
from app.services import (  # noqa: E402
    get_or_create_category,
    get_or_create_manufacturer,
)
from app.models import Part  # noqa: E402
from app.taxonomy import PART_TYPES  # noqa: E402

# יצרן חלפים -> (מדינה, תבנית מק"ט)
BRANDS = {
    "Bosch": ("גרמניה", "0986{n5}"),
    "Mahle": ("גרמניה", "OC{n3}"),
    "Febi Bilstein": ("גרמניה", "{n5}"),
    "Denso": ("יפן", "DCF{n3}P"),
    "NGK": ("יפן", "BKR{n1}E-11"),
    "Valeo": ("צרפת", "{n6}"),
    "Sachs": ("גרמניה", "{n3}268"),
    "TRW": ("בריטניה", "DF{n4}"),
    "Blue Print": ("בריטניה", "ADT{n5}"),
    "SKF": ("שוודיה", "VKBA{n4}"),
}

# סוג חלק -> (יצרנים אפשריים, טווח מחיר עלות, אחריות בחודשים)
SPECS = {
    "brake_disc_front": (["Bosch", "TRW", "Blue Print"], (140, 420), 24),
    "brake_disc_rear": (["Bosch", "TRW", "Blue Print"], (120, 360), 24),
    "brake_pads_front": (["Bosch", "TRW", "Febi Bilstein"], (90, 280), 24),
    "brake_pads_rear": (["Bosch", "TRW", "Febi Bilstein"], (80, 240), 24),
    "oil_filter": (["Mahle", "Bosch", "Blue Print"], (18, 55), 12),
    "air_filter": (["Mahle", "Bosch", "Blue Print"], (25, 90), 12),
    "fuel_filter": (["Mahle", "Bosch"], (45, 190), 12),
    "cabin_filter": (["Mahle", "Bosch", "Blue Print"], (28, 95), 12),
    "timing_belt": (["Bosch", "Febi Bilstein"], (110, 340), 24),
    "serpentine_belt": (["Bosch", "Febi Bilstein"], (45, 150), 12),
    "water_pump": (["Bosch", "Febi Bilstein", "SKF"], (180, 520), 24),
    "thermostat": (["Mahle", "Febi Bilstein"], (55, 180), 12),
    "radiator": (["Valeo", "Mahle"], (380, 1250), 24),
    "spark_plug": (["NGK", "Denso", "Bosch"], (22, 95), 12),
    "ignition_coil": (["Bosch", "Denso"], (140, 420), 24),
    "alternator": (["Bosch", "Valeo", "Denso"], (620, 1850), 24),
    "starter": (["Bosch", "Valeo", "Denso"], (540, 1600), 24),
    "shock_absorber_front": (["Sachs", "TRW", "Febi Bilstein"], (210, 680), 24),
    "shock_absorber_rear": (["Sachs", "TRW", "Febi Bilstein"], (180, 560), 24),
    "control_arm": (["Febi Bilstein", "TRW", "Blue Print"], (190, 620), 24),
    "ball_joint": (["Febi Bilstein", "TRW"], (75, 240), 12),
    "stabilizer_link": (["Febi Bilstein", "TRW", "Blue Print"], (45, 160), 12),
    "wheel_bearing": (["SKF", "Febi Bilstein"], (120, 420), 24),
    "engine_mount": (["Febi Bilstein", "Blue Print"], (140, 480), 24),
    "side_mirror": (["Valeo", "Blue Print"], (260, 980), 12),
    "wiper_blade": (["Bosch", "Valeo"], (35, 120), 6),
    "ac_compressor": (["Valeo", "Denso"], (980, 2900), 24),
    "oxygen_sensor": (["Bosch", "Denso", "NGK"], (280, 890), 12),
    "abs_sensor": (["Bosch", "Febi Bilstein"], (110, 380), 12),
    "fuel_pump": (["Bosch", "Valeo"], (320, 1150), 24),
}

# יצרן רכב -> (שם קצר להתאמה, תבנית מק"ט מקורי)
OEM = {
    "טויוטה": ("Toyota", "{a}{n4}-{n5}"),
    "מאזדה": ("Mazda", "{a}{n2}{a}-{n2}-{n3}"),
    "יונדאי": ("Hyundai", "{n5}-{a}{n3}"),
    "קיה": ("Kia", "{n5}-{a}{n3}"),
    "סקודה": ("Skoda", "{n1}{a}0 {n3} {n3} {a}"),
    "ניסאן": ("Nissan", "{n5}-{a}{a}{n2}{a}"),
    "הונדה": ("Honda", "{n5}-{a}{a}{n1}-{a}{n2}"),
    "פולקסווגן": ("Volkswagen", "{n1}{a}0 {n3} {n3} {a}"),
    "מיצובישי": ("Mitsubishi", "M{a}{n6}"),
    "סוזוקי": ("Suzuki", "{n5}-{n5}"),
}

# (יצרן, דגם, משנה, עד שנה, קוד מנוע, נפח, אילו סוגי חלקים בקטלוג)
VEHICLES = [
    ("טויוטה", "COROLLA", 2013, 2018, "1ZR-FE", "1.6",
     ["brake_disc_front", "brake_pads_front", "brake_pads_rear", "oil_filter",
      "air_filter", "cabin_filter", "spark_plug", "water_pump", "alternator",
      "shock_absorber_front", "wiper_blade", "oxygen_sensor"]),
    ("מאזדה", "MAZDA 3", 2014, 2019, "PE-VPS", "2.0",
     ["brake_disc_front", "brake_pads_front", "oil_filter", "air_filter",
      "cabin_filter", "spark_plug", "ignition_coil", "control_arm",
      "shock_absorber_front", "stabilizer_link", "side_mirror"]),
    ("יונדאי", "I20", 2015, 2020, "G4LC", "1.4",
     ["brake_disc_front", "brake_pads_front", "oil_filter", "air_filter",
      "cabin_filter", "spark_plug", "serpentine_belt", "ball_joint",
      "stabilizer_link", "abs_sensor", "wiper_blade"]),
    ("קיה", "SPORTAGE", 2016, 2021, "G4FJ", "1.6T",
     ["brake_disc_front", "brake_disc_rear", "brake_pads_front", "oil_filter",
      "air_filter", "cabin_filter", "spark_plug", "water_pump", "control_arm",
      "wheel_bearing", "ac_compressor"]),
    ("סקודה", "OCTAVIA", 2013, 2019, "CZCA", "1.4",
     ["brake_disc_front", "brake_pads_front", "brake_pads_rear", "oil_filter",
      "air_filter", "cabin_filter", "timing_belt", "water_pump", "thermostat",
      "engine_mount", "shock_absorber_rear", "fuel_pump"]),
    ("ניסאן", "QASHQAI", 2014, 2021, "HRA2DDT", "1.2",
     ["brake_disc_front", "brake_pads_front", "oil_filter", "air_filter",
      "cabin_filter", "spark_plug", "serpentine_belt", "radiator",
      "shock_absorber_front", "stabilizer_link", "abs_sensor"]),
    ("הונדה", "CIVIC", 2017, 2022, "L15B7", "1.5T",
     ["brake_disc_front", "brake_pads_front", "oil_filter", "air_filter",
      "cabin_filter", "spark_plug", "ignition_coil", "control_arm",
      "wheel_bearing", "oxygen_sensor", "wiper_blade"]),
    ("פולקסווגן", "GOLF", 2012, 2017, "CZDA", "1.4",
     ["brake_disc_front", "brake_pads_front", "oil_filter", "air_filter",
      "cabin_filter", "timing_belt", "water_pump", "thermostat", "alternator",
      "starter", "engine_mount", "shock_absorber_front"]),
    ("מיצובישי", "OUTLANDER", 2012, 2018, "4J12", "2.4",
     ["brake_disc_front", "brake_disc_rear", "brake_pads_front", "oil_filter",
      "air_filter", "spark_plug", "water_pump", "control_arm", "ball_joint",
      "shock_absorber_rear", "ac_compressor"]),
    ("סוזוקי", "SWIFT", 2011, 2016, "K12C", "1.2",
     ["brake_disc_front", "brake_pads_front", "oil_filter", "air_filter",
      "cabin_filter", "spark_plug", "serpentine_belt", "stabilizer_link",
      "shock_absorber_front", "wiper_blade", "starter"]),
]

SUPPLIERS = [
    ("חלפים ישיר בע\"מ", "רועי מזרחי", "03-5551200", "roei@halafim-yashir.example"),
    ("מ.א. יבוא חלפים", "אורית לוי", "04-8887340", "orit@ma-import.example"),
    ("שוקי חלפים", "שוקי בן דוד", "08-9331384", "shuki@shuki-parts.example"),
]

ALPHABET = "ABCDEFGHJKLMNPRSTUVWXYZ"


def _rand(seed, length, alpha=False):
    """מחולל דטרמיניסטי - אותו קלט תמיד מייצר את אותו מק"ט."""
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    if alpha:
        return "".join(ALPHABET[int(digest[i : i + 2], 16) % len(ALPHABET)] for i in range(0, length * 2, 2))
    return "".join(str(int(digest[i], 16) % 10) for i in range(length))


def _fill(template, seed):
    """ממלא תבנית מק"ט: {n3} = 3 ספרות, {a} = אות."""
    out, i, counter = [], 0, 0
    while i < len(template):
        if template[i] == "{":
            close = template.index("}", i)
            token = template[i + 1 : close]
            counter += 1
            if token == "a":
                out.append(_rand(f"{seed}:{counter}", 1, alpha=True))
            else:
                out.append(_rand(f"{seed}:{counter}", int(token[1:])))
            i = close + 1
        else:
            out.append(template[i])
            i += 1
    return "".join(out)


def _price(seed, low, high):
    span = high - low
    offset = int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:6], 16) % (span + 1)
    return round((low + offset) / 5) * 5


def seed(app, reset=True):
    """בונה את קטלוג הדמו.

    reset=True  - מוחק הכל ובונה מחדש (ברירת מחדל, לשימוש מקומי).
    reset=False - בונה רק אם הקטלוג ריק, ולא נוגע בנתונים קיימים.
                  זה המצב שרץ בפריסה, כדי לא למחוק דאטה אמיתי.
    """
    with app.app_context():
        if reset:
            db.drop_all()
            db.create_all()

        if not reset and Part.query.first() is not None:
            print("הקטלוג כבר מכיל מק\"טים - מדלגים על הזריעה.")
            return 0

        # ארגון הדגמה - המחירים והמלאי נתלים עליו, לא על הקטלוג המשותף
        demo_org = Organization.query.filter_by(slug="demo").first()
        if demo_org is None:
            demo_org = Organization(name="מוסך הדגמה", slug="demo", kind="מוסך")
            db.session.add(demo_org)
            db.session.flush()

        for brand, (country, _tpl) in BRANDS.items():
            manufacturer = get_or_create_manufacturer(brand)
            manufacturer.country = country

        suppliers = []
        for name, contact, phone, email in SUPPLIERS:
            supplier = Supplier(
                name=name, contact_name=contact, phone=phone, email=email,
                organization=demo_org,
            )
            db.session.add(supplier)
            suppliers.append(supplier)
        db.session.flush()

        created = 0
        for make, model, year_from, year_to, engine, volume, part_types in VEHICLES:
            oem_brand, oem_template = OEM[make]
            for index, part_type in enumerate(part_types):
                if part_type not in SPECS:
                    continue
                brands, (low, high), warranty = SPECS[part_type]
                brand = brands[index % len(brands)]
                seed_key = f"{make}|{model}|{part_type}|{brand}"

                number = _fill(BRANDS[brand][1], seed_key)
                if Part.query.filter_by(part_number=number).first():
                    number = f"{number}-{_rand(seed_key + 'x', 2)}"

                name_he, name_en, category, _syn = PART_TYPES[part_type]
                cost = _price(seed_key, low, high)
                part = Part(
                    part_number=number,
                    name_he=f"{name_he} {model}",
                    name_en=f"{name_en} {model}",
                    description=f"{name_he} מתאים ל{make} {model} {year_from}-{year_to}, מנוע {engine}.",
                    part_type=part_type,
                    manufacturer=get_or_create_manufacturer(brand),
                    category=get_or_create_category(category),
                    warranty_months=warranty,
                    barcode=f"729{_rand(seed_key + 'bc', 10)}",
                    is_active=True,
                )
                part.org_links = [
                    OrgPart(
                        organization=demo_org,
                        cost=cost,
                        price=round(cost * 1.42 / 5) * 5,
                        currency="ILS",
                        stock_qty=int(_rand(seed_key + "stock", 1)),
                        min_stock=2,
                        location=f"{_rand(seed_key + 'loc', 1, alpha=True)}-{_rand(seed_key + 'loc2', 2)}",
                    )
                ]
                part.cross_refs = [
                    CrossReference(
                        ref_number=_fill(oem_template, seed_key + "oem"),
                        ref_type="OEM",
                        ref_brand=oem_brand,
                    )
                ]
                part.fitments = [
                    Fitment(
                        make=make,
                        model=model,
                        engine_code=engine,
                        engine_volume=volume,
                        fuel="בנזין",
                        year_from=year_from,
                        year_to=year_to,
                    )
                ]
                supplier = suppliers[index % len(suppliers)]
                part.supplier_links = [
                    PartSupplier(
                        supplier=supplier,
                        supplier_sku=f"{supplier.id}-{number}",
                        cost=cost,
                        lead_time_days=1 + index % 4,
                        is_preferred=True,
                    )
                ]
                db.session.add(part)
                created += 1

        db.session.commit()
        print(
            f'נטענו {created} מק"טים, {len(BRANDS)} יצרנים, '
            f'{len(VEHICLES)} דגמי רכב, ותומחרו עבור "{demo_org.name}".'
        )
        return created


if __name__ == "__main__":
    import argparse

    from app import create_app

    parser = argparse.ArgumentParser(description="טעינת קטלוג דמו")
    parser.add_argument(
        "--keep",
        action="store_true",
        help="אל תמחק נתונים קיימים - זרע רק אם הקטלוג ריק",
    )
    args = parser.parse_args()
    seed(create_app(), reset=not args.keep)
