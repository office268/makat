"""לוגיקה עסקית - חיפוש, ייבוא/ייצוא CSV וסטטיסטיקות."""
import csv
import io

from sqlalchemy import and_, func, or_

from . import part_columns
from .models import (
    Category,
    CrossReference,
    Fitment,
    Manufacturer,
    OrgPart,
    Part,
    db,
    squash,
)

CSV_COLUMNS = [
    "part_number",
    "name_he",
    "name_en",
    "description",
    "manufacturer",
    "category",
    "barcode",
    "price",
    "cost",
    "currency",
    "vat_included",
    "stock_qty",
    "min_stock",
    "location",
    "weight_kg",
    "dimensions",
    "warranty_months",
    "side",
    "part_type",
    "image_url",
    "notes",
    "is_active",
    "cross_refs",
    "fitments",
]


def current_org_id():
    """מזהה הארגון של המשתמש המחובר, או None למבקר אנונימי.

    גם מחוץ לבקשה התשובה היא None ולא שגיאה: הביטויים של עמודות
    הקבוצה שואלים מי הארגון בזמן שהם נבנים, ולפעמים הם נבנים בבדיקה
    או בסקריפט שאין בהם בקשה כלל. "אין ארגון" הוא מצב חוקי - הוא
    בדיוק מה שקורה למבקר אנונימי - ואין סיבה שיפיל.
    """
    from flask import has_request_context
    from flask_login import current_user

    if not has_request_context():
        return None
    if current_user.is_authenticated:
        return current_user.organization_id
    return None


def _to_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _to_float(value):
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _to_bool(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "כן", "y"}


def _squash_text(value):
    """שם דגם בצורה שבה משווים אותו: בלי רווחים, בלי מקפים, אותיות קטנות.

    מאגר משרד התחבורה ורשימות החלפים לא מסכימים על הכתיב - "RAV 4" מול
    "RAV4", "C-HR" מול "CHR". רווח ומקף אינם מידע, ומק"ט שנמצא בקטלוג
    אבל לא נמצא בחיפוש לפי מספר רישוי הוא כשל שקט.
    """
    return (value or "").replace(" ", "").replace("-", "").lower()


# אותו כיווץ, בצד של בסיס הנתונים. ההגדרה עצמה ב-models.
_squash = squash


def normalize_make(name):
    """שם יצרן בצורה שבה משווים אותו בין המרשם לקטלוג.

    המרשם כותב "מזדה" והקטלוג "מאזדה" - אותו יצרן, אֵם קריאה אחת הבדל,
    והשוואה מילולית פשוט לא מוצאת אותו. לכן נופלות א' ואותיות כפולות
    (וו -> ו), וההשוואה נעשית על מה שנשאר.

    זו נורמליזציה זהירה בכוונה: על שנים-עשר היצרנים שבקטלוג היא אינה
    ממזגת שניים לאחד, ולכן היא מגשרת על כתיב בלי להמציא התאמות.
    """
    text = (name or "").strip().lower().replace("א", "")
    collapsed = []
    for char in text:
        if not collapsed or collapsed[-1] != char:
            collapsed.append(char)
    return "".join(collapsed)


def catalog_make(make):
    """שם היצרן ככתיבתו בקטלוג, אם הוא מוכר שם בכתיב אחר."""
    wanted = normalize_make(make)
    if not wanted:
        return make
    for (name,) in db.session.query(Fitment.make).distinct():
        if name and normalize_make(name) == wanted:
            return name
    return make


# שם דגם קצר מדי מזהה כל דבר: "3" נמצא בתוך "I30" ובתוך "MAZDA 3" גם
# יחד. משלוש אותיות ומעלה ההתאמה כבר אומרת משהו.
MIN_MODEL_PREFIX = 3


def _model_matches(model):
    """התאמת שם דגם בין המרשם לקטלוג, בשני הכיוונים.

    לפעמים שם הקטלוג ארוך יותר ("COROLLA VERSO" מול "COROLLA"), ולפעמים
    דווקא שם המרשם ("COROLLA HSD SDN" מול "COROLLA"). בדיקה בכיוון אחד
    בלבד הפילה את המקרה השני: 265 מק"טים לקורולה לא נמצאו לרכב שבמרשם
    נקרא COROLLA HSD SDN - לא בדוח, וגרוע מזה, גם בזיהוי לפי מספר רישוי.

    לכן: או ששם המרשם מוכל בשם הקטלוג, או ששם המרשם *מתחיל* בשם הקטלוג.
    ההכלה ההפוכה מוגבלת לתחילת המחרוזת ולשמות באורך סביר, אחרת שם קצר
    היה נדבק לכל דגם שמכיל את אותן אותיות.
    """
    squashed = _squash_text(model)
    catalog = _squash(Fitment.model)
    return or_(
        catalog.like(f"%{squashed}%"),
        and_(
            func.length(catalog) >= MIN_MODEL_PREFIX,
            db.literal(squashed).like(catalog.concat("%")),
        ),
    )


def model_matches_name(registry_model, catalog_model):
    """אותו כלל בדיוק, בצד פייתון. מימוש אחד לוגי, שני ניסוחים."""
    wanted = _squash_text(registry_model)
    catalog = _squash_text(catalog_model)
    if not wanted or not catalog:
        return False
    return wanted in catalog or (
        len(catalog) >= MIN_MODEL_PREFIX and wanted.startswith(catalog)
    )


def _engine_matches(terms):
    """התאמה שמצהירה על אחד המנועים המבוקשים.

    ההשוואה מכווצת (בלי רווחים ומקפים, אותיות קטנות), כי הקטלוג כותב
    "2ZR-FAE" והמרשם "2ZRFAE" - אותו מנוע בשני כתיבים.
    """
    if isinstance(terms, str):
        terms = [terms]
    conditions = []
    for term in terms:
        squashed = _squash_text(term)
        if not squashed:
            continue
        conditions.append(_squash(Fitment.engine_code).like(f"%{squashed}%"))
        conditions.append(_squash(Fitment.engine_volume).like(f"%{squashed}%"))
    return or_(*conditions) if conditions else db.false()


def _engine_unspecified():
    """התאמה שלא אמרה כלום על מנוע.

    בקטלוג שלנו רק כחמישית מההתאמות מציינות מנוע. "לא צוין" הוא חוסר
    מידע ולא הצהרה שהחלק אינו מתאים, ולכן סינון לפי מנוע שמוחק אותן
    היה מוחק את רוב הקטלוג ומחזיר פחות מק"טים נכונים, לא יותר.
    """
    return and_(
        or_(Fitment.engine_code.is_(None), Fitment.engine_code == ""),
        or_(Fitment.engine_volume.is_(None), Fitment.engine_volume == ""),
    )


def search_parts(
    q=None,
    part_type=None,
    category_id=None,
    manufacturer_id=None,
    make=None,
    model=None,
    year=None,
    engine=None,
    in_stock=None,
    low_stock=None,
    active_only=True,
    sort="part_number",
    column_filters=None,
    organization_id=None,
):
    """בונה שאילתת חיפוש מק"טים לפי כל הפילטרים.

    מחיר ומלאי פרטיים לארגון, ולכן סינון או מיון לפיהם דורש
    organization_id. בלעדיו הם מתעלמים בשקט - מבקר אנונימי רואה
    את הקטלוג המשותף בלבד.

    column_filters הוא הסינון שמגיע משורת הכותרות של הטבלה, ממופה לפי
    שם הפרמטר. הכללים עצמם יושבים ב-app/part_columns.py, ליד הגדרת
    העמודה - כך שעמודה חדשה מביאה איתה את הסינון שלה.
    """
    query = Part.query

    if q:
        term = f"%{q.strip()}%"
        cross_subq = (
            db.session.query(CrossReference.part_id)
            .filter(CrossReference.ref_number.ilike(term))
            .subquery()
        )
        query = query.filter(
            or_(
                Part.part_number.ilike(term),
                Part.name_he.ilike(term),
                Part.name_en.ilike(term),
                Part.description.ilike(term),
                Part.barcode.ilike(term),
                Part.id.in_(db.session.query(cross_subq.c.part_id)),
            )
        )

    if part_type:
        query = query.filter(Part.part_type == part_type)

    if category_id:
        # כולל תת-קטגוריות
        child_ids = [
            row[0]
            for row in db.session.query(Category.id).filter(
                Category.parent_id == category_id
            )
        ]
        query = query.filter(Part.category_id.in_([category_id, *child_ids]))

    if manufacturer_id:
        query = query.filter(Part.manufacturer_id == manufacturer_id)

    if make or model or year or engine:
        fit = db.session.query(Fitment.part_id)
        if make:
            # הרכב מגיע בכתיב המרשם, ההתאמות נכתבו בכתיב הקטלוג
            fit = fit.filter(Fitment.make.ilike(catalog_make(make)))
        if model:
            fit = fit.filter(_model_matches(model))
        if engine:
            fit = fit.filter(or_(_engine_matches(engine), _engine_unspecified()))
        if year:
            year = _to_int(year)
            if year:
                fit = fit.filter(
                    or_(Fitment.year_from.is_(None), Fitment.year_from <= year),
                    or_(Fitment.year_to.is_(None), Fitment.year_to >= year),
                )
        query = query.filter(Part.id.in_(fit.subquery().select()))

    if organization_id and (in_stock or low_stock):
        stock_q = db.session.query(OrgPart.part_id).filter(
            OrgPart.organization_id == organization_id
        )
        if in_stock:
            stock_q = stock_q.filter(OrgPart.stock_qty > 0)
        if low_stock:
            stock_q = stock_q.filter(OrgPart.stock_qty <= OrgPart.min_stock)
        query = query.filter(Part.id.in_(stock_q.subquery().select()))

    if active_only:
        query = query.filter(Part.is_active.is_(True))

    # סינון ומיון לפי עמודה. ה-join לשכבה הפרטית נעשה פעם אחת לכל
    # היותר, גם כשגם המיון וגם הסינון זקוקים לה.
    query, org_joined = part_columns.apply_filters(
        query, column_filters or {}, organization_id, False
    )
    query, org_joined, sorted_column, _ = part_columns.apply_sort(
        query, sort, organization_id, org_joined
    )
    if sorted_column is not None:
        return query

    org_sorts = {"price_asc", "price_desc", "stock"}
    if sort in org_sorts and organization_id:
        query, org_joined = part_columns.join_org(query, organization_id, org_joined)
        order = {
            "price_asc": OrgPart.price.asc(),
            "price_desc": OrgPart.price.desc(),
            "stock": OrgPart.stock_qty.desc(),
        }[sort]
        return query.order_by(order)

    sorts = {
        "part_number": Part.part_number.asc(),
        "name": Part.name_he.asc(),
        "newest": Part.created_at.desc(),
    }
    return query.order_by(sorts.get(sort, Part.part_number.asc()))


def _fitment_index():
    """מפת ההתאמות של הקטלוג: (יצרן, דגם מכווץ) -> קבוצות מזהי מק"ט.

    נבנית בשאילתה אחת ומשרתת מאות דגמים. שאילתה נפרדת לכל דגם היא
    בזבוז כשהקטלוג כולו הוא אלפי שורות, וכשרוצים לדרג פערים על מאות
    דגמים היא בכלל לא אפשרות.

    שומרים מזהים ולא מונים, כי אותו מק"ט יכול להתאים גם ל-"COROLLA"
    וגם ל-"COROLLA VERSO"; חיבור מונים היה סופר אותו פעמיים, ואיחוד
    קבוצות סופר אותו פעם אחת - בדיוק כמו search_parts.
    """
    from .taxonomy import WEAR_TYPES

    rows = (
        db.session.query(Fitment.make, Fitment.model, Part.id, Part.part_type)
        .join(Part, Part.id == Fitment.part_id)
        .filter(Part.is_active.is_(True))
        .all()
    )
    index = {}
    for make, model, part_id, part_type in rows:
        key = (normalize_make(make), _squash_text(model))
        total, wear = index.setdefault(key, (set(), set()))
        total.add(part_id)
        if part_type in WEAR_TYPES:
            wear.add(part_id)
    return index


def part_counts_for(pairs):
    """{(יצרן, דגם): (מק"טים, מתוכם מתכלים)} לרשימת רכבים, בשאילתה אחת.

    ההתאמה זהה לזו של search_parts: היצרן מלא, והדגם הוא הכלה בשם
    ההתאמה אחרי כיווץ רווחים ומקפים. אחרת המספר בעמודה לא היה מה
    שנפתח בלחיצה עליו.
    """
    index = _fitment_index()
    counts = {}
    for make, model in set(pairs):
        wanted_make = normalize_make(make)
        total, wear = set(), set()
        for (fit_make, fit_model), (part_ids, wear_ids) in index.items():
            if fit_make == wanted_make and model_matches_name(model, fit_model):
                total |= part_ids
                wear |= wear_ids
        counts[(make, model)] = (len(total), len(wear))
    return counts


def vehicle_part_counts(make, model):
    """(מק"טים מתאימים לרכב, מתוכם מתכלים) לדגם אחד."""
    return part_counts_for([(make, model)])[(make, model)]


def find_by_number(number):
    """מאתר מק"ט לפי המספר שלו או לפי מק"ט מקביל."""
    number = (number or "").strip()
    if not number:
        return None
    part = Part.query.filter(func.lower(Part.part_number) == number.lower()).first()
    if part:
        return part
    ref = CrossReference.query.filter(
        func.lower(CrossReference.ref_number) == number.lower()
    ).first()
    return ref.part if ref else None


def equivalent_parts(part):
    """מחזיר מק"טים אחרים בקטלוג שחולקים מק"ט מקביל עם החלק הנתון."""
    numbers = {ref.ref_number.lower() for ref in part.cross_refs}
    numbers.add(part.part_number.lower())
    if not numbers:
        return []
    matches = (
        db.session.query(CrossReference.part_id)
        .filter(func.lower(CrossReference.ref_number).in_(numbers))
        .distinct()
    )
    part_ids = {row[0] for row in matches} - {part.id}
    if not part_ids:
        return []
    return Part.query.filter(Part.id.in_(part_ids)).all()


def get_or_create_manufacturer(name):
    name = (name or "").strip()
    if not name:
        return None
    obj = Manufacturer.query.filter_by(name=name).first()
    if not obj:
        obj = Manufacturer(name=name)
        db.session.add(obj)
        db.session.flush()
    return obj


def get_or_create_category(full_name):
    """מקבל 'מנוע / סינון' ומחזיר את הקטגוריה (יוצר גם את קטגוריית האב)."""
    full_name = (full_name or "").strip()
    if not full_name:
        return None
    parts = [p.strip() for p in full_name.split("/") if p.strip()]
    parent = None
    obj = None
    for name in parts:
        parent_id = parent.id if parent else None
        obj = Category.query.filter_by(name=name, parent_id=parent_id).first()
        if not obj:
            obj = Category(name=name, parent=parent)
            db.session.add(obj)
            db.session.flush()
        parent = obj
    return obj


def vehicle_makes():
    """רשימת יצרני רכב שקיימים בקטלוג."""
    rows = db.session.query(Fitment.make).distinct().order_by(Fitment.make).all()
    return [row[0] for row in rows if row[0]]


def vehicle_models(make=None):
    query = db.session.query(Fitment.model).distinct()
    if make:
        query = query.filter(Fitment.make.ilike(make))
    rows = query.order_by(Fitment.model).all()
    return [row[0] for row in rows if row[0]]


def stats(organization_id=None):
    """מספרי מפתח לדף הבית.

    מספרי הקטלוג משותפים; מלאי ושווי מלאי שייכים לארגון ומוחזרים
    כאפס כשאין ארגון (מבקר אנונימי).
    """
    total = Part.query.count()
    active = Part.query.filter(Part.is_active.is_(True)).count()

    in_stock = low = 0
    stock_value = 0.0
    if organization_id:
        base = OrgPart.query.filter(OrgPart.organization_id == organization_id)
        in_stock = base.filter(OrgPart.stock_qty > 0).count()
        low = base.filter(OrgPart.stock_qty <= OrgPart.min_stock).count()
        stock_value = (
            db.session.query(func.sum(OrgPart.cost * OrgPart.stock_qty))
            .filter(OrgPart.organization_id == organization_id)
            .scalar()
            or 0.0
        )
    return {
        "parts": total,
        "active_parts": active,
        "in_stock": in_stock,
        "low_stock": low,
        "manufacturers": Manufacturer.query.count(),
        "categories": Category.query.count(),
        "fitments": Fitment.query.count(),
        "cross_refs": CrossReference.query.count(),
        "vehicle_makes": len(vehicle_makes()),
        "stock_value": round(stock_value, 2),
        "org_parts": (
            OrgPart.query.filter_by(organization_id=organization_id).count()
            if organization_id
            else 0
        ),
    }


def parse_cross_refs(raw):
    """'OEM:04152-YZZA1:Toyota; חלופי:W68/3' -> רשימת CrossReference."""
    refs = []
    for chunk in (raw or "").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        fields = [f.strip() for f in chunk.split(":")]
        if len(fields) == 1:
            refs.append(CrossReference(ref_number=fields[0], ref_type="OEM"))
        elif len(fields) == 2:
            refs.append(CrossReference(ref_type=fields[0], ref_number=fields[1]))
        else:
            refs.append(
                CrossReference(
                    ref_type=fields[0], ref_number=fields[1], ref_brand=fields[2]
                )
            )
    return refs


def format_cross_refs(part):
    return "; ".join(
        ":".join(filter(None, [ref.ref_type, ref.ref_number, ref.ref_brand]))
        for ref in part.cross_refs
    )


def parse_fitments(raw):
    """'Toyota:Corolla:2013:2018:1ZR-FE; Mazda:3:2014:2019' -> רשימת Fitment."""
    fitments = []
    for chunk in (raw or "").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        fields = [f.strip() for f in chunk.split(":")]
        fields += [""] * (5 - len(fields))
        make, model, y_from, y_to, engine = fields[:5]
        if not make:
            continue
        fitments.append(
            Fitment(
                make=make,
                model=model or None,
                year_from=_to_int(y_from),
                year_to=_to_int(y_to),
                engine_code=engine or None,
            )
        )
    return fitments


def format_fitments(part):
    return "; ".join(
        ":".join(
            [
                fit.make or "",
                fit.model or "",
                str(fit.year_from or ""),
                str(fit.year_to or ""),
                fit.engine_code or "",
            ]
        )
        for fit in part.fitments
    )


def cross_refs_from_rows(rows):
    """בונה מק"טים מקבילים משדות מקבילים בטופס (רשימות באותו אורך)."""
    numbers = rows.get("cross_ref_number") or []
    types = rows.get("cross_ref_type") or []
    brands = rows.get("cross_ref_brand") or []
    refs = []
    for index, number in enumerate(numbers):
        number = (number or "").strip()
        if not number:
            continue
        refs.append(
            CrossReference(
                ref_number=number,
                ref_type=(types[index] if index < len(types) else "") or "OEM",
                ref_brand=(brands[index] if index < len(brands) else "").strip() or None,
            )
        )
    return refs


def fitments_from_rows(rows):
    """בונה התאמות לרכב משדות מקבילים בטופס."""
    makes = rows.get("fit_make") or []
    models = rows.get("fit_model") or []
    years_from = rows.get("fit_year_from") or []
    years_to = rows.get("fit_year_to") or []
    engines = rows.get("fit_engine") or []

    def at(source, index):
        return (source[index] if index < len(source) else "") or ""

    fitments = []
    for index, make in enumerate(makes):
        make = (make or "").strip()
        if not make:
            continue
        fitments.append(
            Fitment(
                make=make,
                model=at(models, index).strip() or None,
                year_from=_to_int(at(years_from, index)),
                year_to=_to_int(at(years_to, index)),
                engine_code=at(engines, index).strip() or None,
            )
        )
    return fitments


def part_from_row(row, part=None, organization_id=None, rows=None):
    """יוצר או מעדכן מק"ט משורת CSV / טופס.

    שדות הקטלוג נכתבים על Part המשותף. שדות מסחריים - מחיר, עלות,
    מלאי ומיקום - נכתבים על השכבה הפרטית של הארגון, ורק אם נמסר
    organization_id. בלעדיו הם מתעלמים, כדי שלא ייכתב מחיר לקטלוג הגלובלי.
    """
    if part is None:
        part = Part()
        # מוסיפים ל-session לפני קישור היצרן/הקטגוריה, אחרת autoflush מזהיר
        db.session.add(part)
    part.part_number = (row.get("part_number") or "").strip()
    part.name_he = (row.get("name_he") or "").strip()
    part.name_en = (row.get("name_en") or "").strip() or None
    part.description = (row.get("description") or "").strip() or None
    part.barcode = (row.get("barcode") or "").strip() or None
    part.weight_kg = _to_float(row.get("weight_kg"))
    part.dimensions = (row.get("dimensions") or "").strip() or None
    part.warranty_months = _to_int(row.get("warranty_months"))
    part.side = (row.get("side") or "").strip() or None
    part.part_type = (row.get("part_type") or "").strip() or None
    part.image_url = (row.get("image_url") or "").strip() or None
    part.notes = (row.get("notes") or "").strip() or None
    if "is_active" in row and str(row.get("is_active")).strip() != "":
        part.is_active = _to_bool(row.get("is_active"))
    elif part.is_active is None:
        part.is_active = True

    manufacturer = get_or_create_manufacturer(row.get("manufacturer"))
    if manufacturer:
        part.manufacturer = manufacturer
    category = get_or_create_category(row.get("category"))
    if category:
        part.category = category

    # החלפת אוסף בהשמה ישירה מייצרת INSERT לפני ה-DELETE, ואז עדכון
    # שמשאיר את אותם ערכים נופל על אילוץ הייחודיות. מוחקים ומרוקנים קודם.
    #
    # שני פורמטי קלט: הטופס שולח שורות נפרדות (rows), ו-CSV שולח מחרוזת
    # אחת מופרדת בנקודה-פסיק. השורות מנצחות כשהן קיימות.
    if rows is not None and "cross_ref_number" in rows:
        _replace_collection(part.cross_refs, cross_refs_from_rows(rows))
    elif "cross_refs" in row:
        _replace_collection(part.cross_refs, parse_cross_refs(row.get("cross_refs")))

    if rows is not None and "fit_make" in rows:
        _replace_collection(part.fitments, fitments_from_rows(rows))
    elif "fitments" in row:
        _replace_collection(part.fitments, parse_fitments(row.get("fitments")))

    if organization_id:
        db.session.flush()  # דרוש כדי שיהיה part.id לקישור
        set_org_part(part, organization_id, row)
    return part


def _replace_collection(collection, new_items):
    """מחליף אוסף ילדים, ומוודא שהמחיקה מגיעה לבסיס הנתונים לפני ההוספה."""
    if collection:
        collection.clear()
        db.session.flush()
    collection.extend(new_items)


def get_org_part(part, organization_id, create=False):
    """השכבה הפרטית של ארגון על מק"ט. יוצר אותה לפי בקשה."""
    if not organization_id or part is None:
        return None
    link = OrgPart.query.filter_by(
        organization_id=organization_id, part_id=part.id
    ).first()
    if link is None and create:
        link = OrgPart(organization_id=organization_id, part=part)
        db.session.add(link)
    return link


# השדות המסחריים ואיך קוראים כל אחד מהם מהקלט. הרשימה הזאת היא גם
# מה שמחליט אילו שדות בכלל נכתבים - ראה set_org_part.
COMMERCIAL_FIELDS = {
    "price": lambda raw: _to_float(raw) or 0.0,
    "cost": lambda raw: _to_float(raw) or 0.0,
    "currency": lambda raw: (raw or "ILS").strip() or "ILS",
    "vat_included": _to_bool,
    "stock_qty": lambda raw: _to_int(raw) or 0,
    "min_stock": lambda raw: _to_int(raw) or 0,
    "location": lambda raw: (raw or "").strip() or None,
}


def set_org_part(part, organization_id, row):
    """כותב לשכבה הפרטית של הארגון את השדות המסחריים *שנמסרו*.

    רק עמודה שקיימת בקלט נכתבת. קודם נכתבו כל השבעה בכל פעם, וכל אחד
    מהם קרא שדה חסר כאפס: מחירון ספק עם עמודת ``price`` בלבד עדכן את
    המחיר ובאותה נשימה איפס את העלות, את המלאי ואת המיקום במדף של כל
    מק"ט בקובץ. אין מסך שמראה מה נמחק, ואין ממה לשחזר.

    לטופס אין השפעה מהשינוי: דפדפן שולח את כל שדות הטופס, ולכן מה
    שהטופס מציג ממשיך להישמר בדיוק כמו קודם. מה שהטופס *אינו* מציג
    (מטבע, כולל מע"מ) פשוט מפסיק להתאפס בכל שמירה.
    """
    present = [field for field in COMMERCIAL_FIELDS if field in row]
    if not present:
        return None

    link = get_org_part(part, organization_id, create=True)
    for field in present:
        setattr(link, field, COMMERCIAL_FIELDS[field](row.get(field)))
    return link


def import_csv(stream, organization_id=None):
    """מייבא CSV. מחזיר (נוספו, עודכנו, שגיאות).

    המחירים והמלאי שבקובץ נכתבים לשכבה הפרטית של הארגון המייבא.
    """
    text = stream.read()
    if isinstance(text, bytes):
        text = text.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    created = updated = 0
    errors = []
    for line_no, row in enumerate(reader, start=2):
        row = {(k or "").strip(): (v or "") for k, v in row.items()}
        number = (row.get("part_number") or "").strip()
        if not number:
            errors.append(f"שורה {line_no}: חסר מק\"ט")
            continue
        if not (row.get("name_he") or "").strip():
            errors.append(f"שורה {line_no}: חסר שם לחלק {number}")
            continue
        existing = Part.query.filter_by(part_number=number).first()
        # נקודת שמירה לכל שורה. בלעדיה שורה פגומה בסוף הקובץ גררה
        # rollback על *כל* מה שהצטבר מתחילת הייבוא: המונים המשיכו לספור,
        # המסך בישר "יובאו 400 מק"טים", ובבסיס הנתונים נשארו ארבעה.
        # כאן נופלת רק השורה שנפלה, ומה שלפניה מגיע לשמירה הסופית.
        try:
            with db.session.begin_nested():
                part_from_row(row, existing, organization_id=organization_id)
        except Exception as exc:  # שורה פגומה - היא בלבד יורדת
            errors.append(f"שורה {line_no}: {exc}")
            continue
        if existing:
            updated += 1
        else:
            created += 1
    db.session.commit()
    return created, updated, errors


def export_csv(parts, organization_id=None):
    """מייצא רשימת מק"טים ל-CSV (עם BOM כדי שאקסל יציג עברית נכון).

    עמודות המחיר והמלאי מתמלאות מהשכבה הפרטית של הארגון; בלי ארגון
    הן יוצאות ריקות, כדי שייצוא אנונימי לא ידלוף מחירים.
    """
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    for part in parts:
        org_part = part.for_org(organization_id)
        writer.writerow(
            {
                "part_number": part.part_number,
                "name_he": part.name_he,
                "name_en": part.name_en or "",
                "description": part.description or "",
                "manufacturer": part.manufacturer.name if part.manufacturer else "",
                "category": part.category.full_name if part.category else "",
                "barcode": part.barcode or "",
                "price": (org_part.price or 0) if org_part else "",
                "cost": (org_part.cost or 0) if org_part else "",
                "currency": (org_part.currency or "ILS") if org_part else "",
                "vat_included": int(bool(org_part.vat_included)) if org_part else "",
                "stock_qty": (org_part.stock_qty or 0) if org_part else "",
                "min_stock": (org_part.min_stock or 0) if org_part else "",
                "location": (org_part.location or "") if org_part else "",
                "weight_kg": part.weight_kg or "",
                "dimensions": part.dimensions or "",
                "warranty_months": part.warranty_months or "",
                "side": part.side or "",
                "part_type": part.part_type or "",
                "image_url": part.image_url or "",
                "notes": part.notes or "",
                "is_active": int(bool(part.is_active)),
                "cross_refs": format_cross_refs(part),
                "fitments": format_fitments(part),
            }
        )
    return "﻿" + buffer.getvalue()


def vehicle_engine_terms(vehicle):
    """באילו מונחים אפשר לזהות את המנוע של הרכב הזה בתוך הקטלוג.

    הקטלוג מדבר שני כתיבים: רובו נפח ורמת מנוע ("1.4 TSI"), ומיעוטו
    קוד יצרן ("2ZR-FAE"). המרשם מוסר קוד יצרן בלבד, והנפח מגיע מקטלוג
    הדגמים שלנו לפי (יצרן, קוד דגם) - 1398 סמ"ק הופכים ל-"1.4".

    מוחזרים שני המונחים, כי כל אחד מהם מוצא חלק אחר של הקטלוג.
    """
    from .vehicle_catalog import VehicleModel

    terms = []
    code = (vehicle.get("engine_code") or "").strip()
    if code:
        terms.append(code)

    make = (vehicle.get("make") or "").strip().split()
    model_code = (vehicle.get("model_code") or "").strip()
    if make and model_code:
        row = VehicleModel.query.filter(
            VehicleModel.make.ilike(f"%{make[0]}%"),
            VehicleModel.model_code == model_code,
            VehicleModel.engine_volume.isnot(None),
        ).first()
        if row is not None and row.engine_volume:
            terms.append(f"{round(row.engine_volume / 1000, 1):.1f}")
    return terms


def parts_for_vehicle(vehicle, part_type=None):
    """ההצטלבות: רכב מזוהה × סוג חלק -> המק"טים המתאימים בלבד."""
    if not vehicle:
        return []
    make = (vehicle.get("make") or "").strip().split()
    return (
        search_parts(
            part_type=part_type,
            make=make[0] if make else None,
            model=vehicle.get("model"),
            year=vehicle.get("year"),
        ).all()
        if make
        else []
    )


def engine_matched_parts(parts, terms):
    """מזהי המק"טים שההתאמה שלהם מצהירה במפורש על המנוע של הרכב.

    זה סימון ולא סינון, בכוונה: רק כחמישית מההתאמות בקטלוג מציינות
    מנוע, והן מציינות אותו בשני כתיבים שונים. סינון היה מסתיר מק"ט
    נכון שנכתב בכתיב האחר - הפסד גרוע בהרבה מרשימה קצת ארוכה. אז
    כולם נשארים, והמאומתים עולים לראש ומסומנים.
    """
    if not terms or not parts:
        return set()
    ids = [part.id for part in parts]
    rows = (
        db.session.query(Fitment.part_id)
        .filter(Fitment.part_id.in_(ids), _engine_matches(terms))
        .distinct()
    )
    return {row[0] for row in rows}


def catalog_coverage(vehicle):
    """אילו סוגי חלקים קיימים בקטלוג עבור הרכב הזה - לשקיפות במסך הזיהוי."""
    parts = parts_for_vehicle(vehicle)
    seen = {}
    for part in parts:
        seen.setdefault(part.part_type, 0)
        seen[part.part_type] += 1
    return seen


def low_stock_parts(organization_id, limit=8):
    """מק"טים שהמלאי שלהם מתחת למינימום, בארגון נתון."""
    if not organization_id:
        return []
    return (
        OrgPart.query.filter(
            OrgPart.organization_id == organization_id,
            OrgPart.stock_qty <= OrgPart.min_stock,
        )
        .order_by(OrgPart.stock_qty)
        .limit(limit)
        .all()
    )


def column_counts(parts, columns, organization_id=None):
    """הספירות שהעמודות המחושבות מציגות, לשורות שעל המסך בלבד.

    {מזהה מק"ט: {"catalog_parts": n, "substitutes": n}}

    שאילתה אחת לכל הדף, ובאותם ביטויים שלפיהם ממיינים ומסננים - אחרת
    המספר שבתא היה יכול לסתור את הסדר שהוא עצמו יצר. כשאף אחת משתי
    העמודות אינה מוצגת, אין כאן שאילתה בכלל.
    """
    keys = {column.key for column in columns}
    counted = {"catalog_parts", "substitutes"} & keys
    # "ביקוש" מציג את אותם מספרי צי בתא אחד, ולכן צריך אותם בדיוק כמו
    # שתי עמודות הצי הנפרדות
    fleet_keys = {"fleet_vehicles", "fleet_prime", "fleet_gap", "demand"} & keys
    group_keys = {"group_stock", "group_price"} & keys
    if not parts or not (counted or fleet_keys or group_keys):
        return {}

    values = {part.id: {} for part in parts}
    if counted:
        rows = (
            db.session.query(
                Part.id,
                part_columns.CATALOG_PARTS,
                part_columns.SUBSTITUTES,
            )
            .filter(Part.id.in_(list(values)))
            .all()
        )
        for part_id, catalog, substitutes in rows:
            values[part_id].update(
                catalog_parts=catalog or 0, substitutes=substitutes or 0
            )
    if fleet_keys:
        values = _add_fleet_numbers(values, parts)
    if group_keys:
        values = _add_group_numbers(values, parts, organization_id)
    return values


def _add_group_numbers(values, parts, organization_id=None):
    """המלאי והמחירים של המק"ט יחד עם כל תחליפיו.

    שאילתה אחת לכל הדף, ובאותם ביטויים שלפיהם ממיינים - התא והסדר
    חייבים לומר את אותו דבר.

    "מקורי" נקבע לפי הסימן היחיד שיש בנתונים: יצרן החלק זהה למותג
    שרשום על המק"ט המקורי. בקטלוג היום זה כמעט לא קורה, ולכן התא
    יישאר ריק ברוב השורות - וזה עדיף על ניחוש.
    """
    if organization_id is None:
        organization_id = current_org_id()
    if not organization_id:
        return values

    rows = (
        db.session.query(
            Part.id,
            part_columns.group_stock(organization_id),
            part_columns.group_cheapest(organization_id),
            part_columns.group_dearest(organization_id),
        )
        .filter(Part.id.in_(list(values)))
        .all()
    )
    for part_id, stock, cheapest, dearest in rows:
        values[part_id].update(
            group_stock=stock or 0,
            cheapest=cheapest,
            dearest=dearest,
        )
    for part in parts:
        values[part.id]["original_price"] = (
            _own_price(part, organization_id) if is_original_part(part) else None
        )
    return values


def _own_price(part, organization_id):
    for link in part.org_links:
        if link.organization_id == organization_id:
            return link.price_with_vat
    return None


def is_original_part(part):
    """האם המק"ט הזה הוא החלק המקורי של יצרן הרכב, ולא תחליפי.

    אין במערכת שדה שאומר את זה. הסימן היחיד שיש: החלק נושא את שם
    היצרן שרשום על המק"ט המקורי שלו - כלומר "TOYOTA" שמוכר חלק
    שהמקור שלו רשום על שם Toyota. חלק של BOSCH לאותו מספר הוא תחליפי.
    """
    maker = (part.manufacturer.name if part.manufacturer else "").strip().lower()
    if not maker:
        return False
    for ref in part.cross_refs:
        brand = (ref.ref_brand or "").strip().lower()
        if not brand or ref.ref_type != "OEM":
            continue
        if maker == brand or maker in brand or brand in maker:
            return True
    return False


def _add_fleet_numbers(values, parts):
    """מוסיף לכל שורה את מספרי הצי של הרכב שהחלק מתאים לו.

    מפייתון ולא מ-SQL, ומאותה מפה שממנה נבנה גם ביטוי המיון - כך התא
    והסדר אומרים את אותו דבר. בלי צילום צי אין מספרים, וזה "—" ולא
    אפס: "לא ידוע" אינו "אין רכבים כאלה".
    """
    from . import fleet_stats

    numbers = fleet_stats.catalog_fleet_numbers()
    for part in parts:
        if not numbers:
            values[part.id].update(fleet_vehicles=None, fleet_prime=None, fleet_gap=None)
            continue
        # הגדול מבין הרכבים שהחלק מתאים להם - הנפוץ שבהם. אותו כלל
        # בדיוק כמו ה-CASE שממיין (ראה part_columns.fleet_value).
        best = {"vehicles": 0, "prime": 0, "gap": 0.0}
        for fitment in part.fitments:
            row = numbers.get((fitment.make, fitment.model))
            if row:
                for field in best:
                    best[field] = max(best[field], row[field])
        values[part.id].update(
            fleet_vehicles=best["vehicles"],
            fleet_prime=best["prime"],
            fleet_gap=best["gap"],
        )
    return values


# ---------- פריסת העמודות ----------

PARTS_TABLE = "parts"
SALES_TABLE = "parts_sales"


def column_layout(table_key=PARTS_TABLE):
    """העמודות המוצגות בטבלה, לפי סדרן.

    אין שורה שמורה = ברירת המחדל שבקוד. כך הטבלה עובדת ביום הראשון,
    לפני שמנהל האפליקציה נגע בה בכלל.
    """
    from .models import TableLayout

    fallback = (
        part_columns.SALES_KEYS
        if table_key == SALES_TABLE
        else part_columns.DEFAULT_KEYS
    )
    layout = TableLayout.query.filter_by(table_key=table_key).first()
    return part_columns.resolve(layout.keys if layout else fallback, fallback)


def save_column_layout(keys, table_key=PARTS_TABLE, user=None):
    """שומר את הפריסה. רשימה ריקה תחזיר את ברירת המחדל בקריאה הבאה."""
    from .models import TableLayout

    layout = TableLayout.query.filter_by(table_key=table_key).first()
    if layout is None:
        layout = TableLayout(table_key=table_key)
        db.session.add(layout)
    layout.keys = list(keys)
    if user is not None:
        layout.updated_by_id = user.id
    db.session.commit()
    return layout
