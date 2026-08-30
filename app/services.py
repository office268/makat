"""לוגיקה עסקית - חיפוש, ייבוא/ייצוא CSV וסטטיסטיקות."""
import csv
import io

from sqlalchemy import func, or_

from .models import (
    Category,
    CrossReference,
    Fitment,
    Manufacturer,
    OrgPart,
    Part,
    db,
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
    """מזהה הארגון של המשתמש המחובר, או None למבקר אנונימי."""
    from flask_login import current_user

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


def _squash(column):
    """אותו כיווץ, בצד של בסיס הנתונים. replace ו-lower קיימים בשניהם."""
    return func.replace(func.replace(func.lower(column), " ", ""), "-", "")


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
    organization_id=None,
):
    """בונה שאילתת חיפוש מק"טים לפי כל הפילטרים.

    מחיר ומלאי פרטיים לארגון, ולכן סינון או מיון לפיהם דורש
    organization_id. בלעדיו הם מתעלמים בשקט - מבקר אנונימי רואה
    את הקטלוג המשותף בלבד.
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
            fit = fit.filter(Fitment.make.ilike(make))
        if model:
            fit = fit.filter(_squash(Fitment.model).like(f"%{_squash_text(model)}%"))
        if engine:
            fit = fit.filter(
                or_(
                    Fitment.engine_code.ilike(f"%{engine}%"),
                    Fitment.engine_volume.ilike(f"%{engine}%"),
                )
            )
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

    org_sorts = {"price_asc", "price_desc", "stock"}
    if sort in org_sorts and organization_id:
        query = query.outerjoin(
            OrgPart,
            db.and_(
                OrgPart.part_id == Part.id,
                OrgPart.organization_id == organization_id,
            ),
        )
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


def set_org_part(part, organization_id, row):
    """כותב את השדות המסחריים לשכבה הפרטית של הארגון."""
    commercial = {"price", "cost", "currency", "vat_included",
                  "stock_qty", "min_stock", "location"}
    if not commercial & set(row):
        return None

    link = get_org_part(part, organization_id, create=True)
    link.price = _to_float(row.get("price")) or 0.0
    link.cost = _to_float(row.get("cost")) or 0.0
    link.currency = (row.get("currency") or "ILS").strip() or "ILS"
    link.vat_included = _to_bool(row.get("vat_included"))
    link.stock_qty = _to_int(row.get("stock_qty")) or 0
    link.min_stock = _to_int(row.get("min_stock")) or 0
    link.location = (row.get("location") or "").strip() or None
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
        try:
            part = part_from_row(row, existing, organization_id=organization_id)
            if existing:
                updated += 1
            else:
                db.session.add(part)
                created += 1
            db.session.flush()
        except Exception as exc:  # pragma: no cover - הגנה על ייבוא פגום
            db.session.rollback()
            errors.append(f"שורה {line_no}: {exc}")
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
