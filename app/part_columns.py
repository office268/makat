"""העמודות של טבלת המק"טים: מה יש, איך ממיינים ואיך מסננים.

מקור אמת אחד. הכותרת, המיון, הסינון והערך שמוצג בתא - כולם יוצאים
מהרשימה הזאת, כך שהוספת עמודה היא שורה אחת ולא סיור בין תבנית,
מסלול ושאילתה.

הסדר ואילו עמודות מוצגות אינם כאן אלא בבסיס הנתונים (TableLayout),
כי מנהל האפליקציה קובע אותם מהמסך - בלי פריסה מחדש.
"""
import re

from sqlalchemy import case

from .models import (
    Category,
    CrossReference,
    Fitment,
    Manufacturer,
    OrgPart,
    Part,
    db,
)

# הביטוי שמחשב מחיר כולל מע"מ בצד בסיס הנתונים. חייב להתאים ל-
# OrgPart.price_with_vat, אחרת המיון יסתור את מה שכתוב בעמודה.
PRICE_WITH_VAT = case((OrgPart.vat_included.is_(True), OrgPart.price), else_=OrgPart.price * 1.18)

# ">100", "<=50", "10-20" או "7". מה שאי אפשר לקרוא כמספר לא מסנן כלום -
# מסננים שהוקלדו למחצה לא אמורים לרוקן את הטבלה.
_COMPARISON = re.compile(r"^(>=|<=|>|<|=)?\s*(-?\d+(?:\.\d+)?)$")
_RANGE = re.compile(r"^(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)$")


def number_condition(column, raw):
    """תנאי מספרי מתוך מה שהוקלד, או None כשזה לא מספר."""
    text = (raw or "").strip()
    if not text:
        return None
    match = _RANGE.match(text)
    if match:
        return column.between(float(match.group(1)), float(match.group(2)))
    match = _COMPARISON.match(text)
    if not match:
        return None
    value = float(match.group(2))
    return {
        ">": column > value,
        ">=": column >= value,
        "<": column < value,
        "<=": column <= value,
        "=": column == value,
    }[match.group(1) or "="]


def _text_condition(column, raw):
    return column.ilike(f"%{raw.strip()}%")


def _id_condition(column, raw):
    """סינון לפי מזהה מרשימה נפתחת. ערך שאינו מספר לא מסנן כלום."""
    try:
        return column == int(raw)
    except (TypeError, ValueError):
        return None


def _category_condition(raw):
    """קטגוריה כוללת את תת-הקטגוריות שלה, כמו בסרגל הסינון הכללי."""
    try:
        category_id = int(raw)
    except (TypeError, ValueError):
        return None
    children = [
        row[0]
        for row in db.session.query(Category.id).filter(
            Category.parent_id == category_id
        )
    ]
    return Part.category_id.in_([category_id, *children])


def _in_related(model, condition):
    """מק"טים שיש להם שורה קשורה שעונה על התנאי."""
    return Part.id.in_(
        db.session.query(model.part_id).filter(condition).subquery().select()
    )


class Column:
    """עמודה אחת בטבלה.

    sort_by    מה ממיינים לפיו. None = העמודה אינה ניתנת למיון (רשימה
               של ערכים, כמו ההתאמות, אין לה סדר אחד נכון)
    param      שם הפרמטר של הסינון בשורת הכתובת
    kind       "text" / "number" / "select" - איך נראה שדה הסינון
    needs_org  הערך יושב בשכבה הפרטית של הארגון (מחיר, מלאי, מיקום)
    """

    def __init__(self, key, label, sort_by=None, join=None, param=None, kind=None,
                 apply=None, options=None, align="", needs_org=False, text=None,
                 hint=None):
        self.key = key
        self.label = label
        self.sort_by = sort_by
        self.join = join
        self.param = param
        self.kind = kind
        self._apply = apply
        self._options = options
        self.align = align
        self.needs_org = needs_org
        self._text = text
        self.hint = hint

    @property
    def sortable(self):
        return self.sort_by is not None

    @property
    def filterable(self):
        return self.param is not None

    def condition(self, raw):
        """תנאי ה-WHERE של העמודה, או None כשאין מה לסנן."""
        if self._apply is None or not (raw or "").strip():
            return None
        return self._apply(raw)

    def options(self):
        return self._options() if self._options else []

    def text(self, part, org_part):
        """הערך כטקסט. התאים העשירים מצוירים בתבנית עצמה."""
        if self._text is None:
            return ""
        return self._text(part, org_part)


COLUMNS = (
    Column(
        "part_number", 'מק"ט',
        sort_by=Part.part_number, param="f_part_number", kind="text",
        apply=lambda raw: _text_condition(Part.part_number, raw),
    ),
    Column(
        "oem", 'מק"ט מקורי',
        # רשימה של מספרים, ולכן אין לה סדר אחד נכון - אבל בהחלט מחפשים בה
        param="f_oem", kind="text",
        apply=lambda raw: _in_related(
            CrossReference,
            db.and_(
                CrossReference.ref_type == "OEM",
                CrossReference.ref_number.ilike(f"%{raw.strip()}%"),
            ),
        ),
    ),
    Column(
        "name_he", "שם",
        sort_by=Part.name_he, param="f_name_he", kind="text",
        apply=lambda raw: _text_condition(Part.name_he, raw),
    ),
    Column(
        "name_en", "שם באנגלית",
        sort_by=Part.name_en, param="f_name_en", kind="text",
        apply=lambda raw: _text_condition(Part.name_en, raw),
        text=lambda part, op: part.name_en or "—",
    ),
    Column(
        "manufacturer", "יצרן",
        sort_by=Manufacturer.name, join=Part.manufacturer,
        param="f_manufacturer", kind="select",
        apply=lambda raw: _id_condition(Part.manufacturer_id, raw),
        options=lambda: [
            (m.id, m.name) for m in Manufacturer.query.order_by(Manufacturer.name)
        ],
        text=lambda part, op: part.manufacturer.name if part.manufacturer else "—",
    ),
    Column(
        "category", "קטגוריה",
        sort_by=Category.name, join=Part.category,
        param="f_category", kind="select",
        apply=lambda raw: _category_condition(raw),
        options=lambda: [
            (c.id, c.full_name) for c in Category.query.order_by(Category.name)
        ],
        text=lambda part, op: part.category.name if part.category else "—",
    ),
    Column(
        "part_type", "סוג חלק",
        sort_by=Part.part_type, param="f_part_type", kind="select",
        apply=lambda raw: Part.part_type == raw.strip(),
        options=lambda: _part_type_options(),
        text=lambda part, op: _type_name(part.part_type),
    ),
    Column(
        "fitments", "מתאים ל",
        param="f_fitment", kind="text",
        apply=lambda raw: _in_related(
            Fitment,
            db.or_(
                Fitment.make.ilike(f"%{raw.strip()}%"),
                Fitment.model.ilike(f"%{raw.strip()}%"),
            ),
        ),
        hint="יצרן או דגם",
    ),
    Column(
        "barcode", "ברקוד",
        sort_by=Part.barcode, param="f_barcode", kind="text",
        apply=lambda raw: _text_condition(Part.barcode, raw),
        text=lambda part, op: part.barcode or "—",
    ),
    Column(
        "side", "צד",
        sort_by=Part.side, param="f_side", kind="text",
        apply=lambda raw: _text_condition(Part.side, raw),
        text=lambda part, op: part.side or "—",
    ),
    Column(
        "dimensions", "מידות",
        sort_by=Part.dimensions, param="f_dimensions", kind="text",
        apply=lambda raw: _text_condition(Part.dimensions, raw),
        text=lambda part, op: part.dimensions or "—",
    ),
    Column(
        "weight_kg", 'משקל (ק"ג)',
        sort_by=Part.weight_kg, param="f_weight", kind="number",
        apply=lambda raw: number_condition(Part.weight_kg, raw),
        align="text-end",
        text=lambda part, op: part.weight_kg if part.weight_kg is not None else "—",
    ),
    Column(
        "warranty_months", "אחריות (חודשים)",
        sort_by=Part.warranty_months, param="f_warranty", kind="number",
        apply=lambda raw: number_condition(Part.warranty_months, raw),
        align="text-end",
        text=lambda part, op: part.warranty_months if part.warranty_months is not None else "—",
    ),
    Column(
        "price", 'מחיר כולל מע"מ',
        sort_by=PRICE_WITH_VAT, param="f_price", kind="number",
        apply=lambda raw: number_condition(PRICE_WITH_VAT, raw),
        align="text-end", needs_org=True,
    ),
    Column(
        "cost", "עלות",
        sort_by=OrgPart.cost, param="f_cost", kind="number",
        apply=lambda raw: number_condition(OrgPart.cost, raw),
        align="text-end", needs_org=True,
    ),
    Column(
        "stock", "מלאי",
        sort_by=OrgPart.stock_qty, param="f_stock", kind="number",
        apply=lambda raw: number_condition(OrgPart.stock_qty, raw),
        align="text-center", needs_org=True,
    ),
    Column(
        "min_stock", "מלאי מינימלי",
        sort_by=OrgPart.min_stock, param="f_min_stock", kind="number",
        apply=lambda raw: number_condition(OrgPart.min_stock, raw),
        align="text-end", needs_org=True,
        text=lambda part, op: op.min_stock if op else "—",
    ),
    Column(
        "location", "מיקום במחסן",
        sort_by=OrgPart.location, param="f_location", kind="text",
        apply=lambda raw: _text_condition(OrgPart.location, raw),
        needs_org=True,
        text=lambda part, op: (op.location if op and op.location else "—"),
    ),
    Column(
        "created_at", "נוצר ב",
        sort_by=Part.created_at, align="text-end",
    ),
)

BY_KEY = {column.key: column for column in COLUMNS}

# מה שמוצג כשאיש עוד לא נגע בפריסה - בדיוק הטבלה שהייתה כאן קודם
DEFAULT_KEYS = (
    "part_number", "oem", "name_he", "manufacturer", "fitments", "price", "stock",
)


def _type_name(key):
    from .taxonomy import type_name

    return type_name(key) if key else "—"


def _part_type_options():
    from .taxonomy import all_types

    return [(entry["key"], entry["name_he"]) for entry in all_types()]


def by_key(key):
    return BY_KEY.get(key)


def resolve(keys):
    """רשימת מפתחות -> עמודות. מפתח שאינו מוכר נופל בשקט.

    פריסה שמורה עשויה להצביע על עמודה שהוסרה מהקוד; טבלה שנשברת
    בגלל זה גרועה מטבלה שחסרה בה עמודה.
    """
    columns = [BY_KEY[key] for key in keys if key in BY_KEY]
    return columns or [BY_KEY[key] for key in DEFAULT_KEYS]


# שמות המיון שקדמו לעמודות. "stock" לבדו פירושו מלאי *יורד*, ולכן הוא
# לא יכול להתפרש כמיון עולה לפי עמודת המלאי רק מפני שיש עמודה בשם הזה.
# מיון של עמודה נכתב תמיד עם כיוון: "stock:asc".
LEGACY_SORTS = frozenset(
    {"part_number", "name", "newest", "price_asc", "price_desc", "stock"}
)


def parse_sort(raw):
    """'stock:desc' -> (העמודה, 'desc'). כל השאר -> (None, 'asc')."""
    key, separator, direction = (raw or "").partition(":")
    if not separator and key in LEGACY_SORTS:
        return None, "asc"
    column = BY_KEY.get(key)
    if column is None or not column.sortable:
        return None, "asc"
    return column, ("desc" if direction == "desc" else "asc")


def apply_sort(query, raw, organization_id, org_joined):
    """מיון לפי עמודה. מחזיר את השאילתה ואת מצב ה-join של השכבה הפרטית."""
    column, direction = parse_sort(raw)
    if column is None:
        return query, org_joined, None, "asc"
    if column.needs_org:
        if not organization_id:
            return query, org_joined, None, "asc"
        query, org_joined = join_org(query, organization_id, org_joined)
    if column.join is not None:
        query = query.outerjoin(column.join)
    order = column.sort_by.desc() if direction == "desc" else column.sort_by.asc()
    return query.order_by(order), org_joined, column, direction


def apply_filters(query, values, organization_id, org_joined):
    """סינון לפי עמודות. values ממופה לפי שם הפרמטר."""
    for column in COLUMNS:
        if not column.filterable:
            continue
        condition = column.condition(values.get(column.param))
        if condition is None:
            continue
        if column.needs_org:
            if not organization_id:
                continue
            query, org_joined = join_org(query, organization_id, org_joined)
        query = query.filter(condition)
    return query, org_joined


def join_org(query, organization_id, already):
    """מחבר את השכבה הפרטית פעם אחת בלבד."""
    if already:
        return query, True
    return (
        query.outerjoin(
            OrgPart,
            db.and_(
                OrgPart.part_id == Part.id,
                OrgPart.organization_id == organization_id,
            ),
        ),
        True,
    )
