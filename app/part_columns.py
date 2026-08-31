"""העמודות של טבלת המק"טים: מה יש, איך ממיינים ואיך מסננים.

מקור אמת אחד. הכותרת, המיון, הסינון והערך שמוצג בתא - כולם יוצאים
מהרשימה הזאת, כך שהוספת עמודה היא שורה אחת ולא סיור בין תבנית,
מסלול ושאילתה.

הסדר ואילו עמודות מוצגות אינם כאן אלא בבסיס הנתונים (TableLayout),
כי מנהל האפליקציה קובע אותם מהמסך - בלי פריסה מחדש.
"""
import re

from sqlalchemy import case, cast, func, union

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

# הביטוי שמחשב מחיר כולל מע"מ בצד בסיס הנתונים. חייב להתאים ל-
# OrgPart.price_with_vat, אחרת המיון יסתור את מה שכתוב בעמודה.
PRICE_WITH_VAT = case((OrgPart.vat_included.is_(True), OrgPart.price), else_=OrgPart.price * 1.18)

# ---------- ספירות שמחושבות בבסיס הנתונים ----------
#
# שתי העמודות שסופרות שורות אחרות בקטלוג - כמה חלפים יש לרכב הזה,
# וכמה מק"טים שקולים יש לחלק הזה - מקובצות פעם אחת בתת-שאילתה ולא
# נספרות מחדש לכל שורה. זה ההבדל בין מיון שלוקח רגע לבין מיון שסורק
# את הקטלוג פעם לכל מק"ט.

# כמה מק"טים בקטלוג מתאימים לכל (יצרן, דגם). הכיווץ זהה לזה שבחיפוש,
# כך ש-"RAV 4" ו-"RAV4" נספרים יחד.
_PARTS_PER_VEHICLE = (
    db.select(
        squash(Fitment.make).label("make"),
        squash(Fitment.model).label("model"),
        func.count(func.distinct(Fitment.part_id)).label("parts"),
    )
    .group_by(squash(Fitment.make), squash(Fitment.model))
    .subquery()
)

# חלק מתאים לפעמים לכמה רכבים. המספר הוא של הרכב שיש לו הכי הרבה
# חלפים בקטלוג - הרחב מביניהם, ולא סכום שסופר את אותו מק"ט פעמיים.
#
# ה-coalesce אינו קישוט: מק"ט בלי התאמה לרכב מחזיר NULL, והתא מציג
# עליו 0. בלי ההשוואה הזאת המיון היה סותר את מה שכתוב במסך - ולא
# באותה צורה בשני בסיסי הנתונים: SQLite ממיין NULL ראשון ו-Postgres
# אחרון, כך שאותה לחיצה הייתה נותנת שתי תשובות שונות.
CATALOG_PARTS = func.coalesce(
    db.select(func.max(_PARTS_PER_VEHICLE.c.parts))
    .select_from(Fitment)
    .join(
        _PARTS_PER_VEHICLE,
        db.and_(
            _PARTS_PER_VEHICLE.c.make == squash(Fitment.make),
            _PARTS_PER_VEHICLE.c.model == squash(Fitment.model),
        ),
    )
    .where(Fitment.part_id == Part.id)
    .correlate(Part)
    .scalar_subquery(),
    0,
)

# תחליף = מק"ט אחר בקטלוג שחולק מספר מקביל עם החלק הזה. אותו כלל
# בדיוק כמו רשימת "מק"טים שקולים בקטלוג" שבכרטיס המק"ט
# (services.equivalent_parts): המספרים שלי הם המקבילים שלי *וגם*
# המק"ט שלי עצמו, והם נבדקים מול המקבילים של האחרים.
_MY_NUMBERS = union(
    db.select(
        CrossReference.part_id.label("part_id"),
        func.lower(CrossReference.ref_number).label("ref"),
    ),
    db.select(Part.id.label("part_id"), func.lower(Part.part_number).label("ref")),
).subquery()

# הספירה לכל המק"טים בבת אחת. הניסוח המתבקש - תת-שאילתה שרצה מחדש
# לכל שורה - לקח שבע שניות על קטלוג של 2,400 מק"טים, כי lower() מבטל
# את האינדקס והחיפוש הופך לסריקה מלאה לכל מק"ט. כאן הצירוף נעשה פעם
# אחת ומקובץ, וזה יורד לעשרות אלפיות.
_SUBSTITUTES_PER_PART = (
    db.select(
        _MY_NUMBERS.c.part_id.label("part_id"),
        func.count(func.distinct(CrossReference.part_id)).label("parts"),
    )
    .select_from(_MY_NUMBERS)
    .join(
        CrossReference,
        db.and_(
            func.lower(CrossReference.ref_number) == _MY_NUMBERS.c.ref,
            CrossReference.part_id != _MY_NUMBERS.c.part_id,
        ),
    )
    .group_by(_MY_NUMBERS.c.part_id)
    .subquery()
)

# מק"ט בלי מקבילים אינו "חסר ערך" אלא אפס תחליפים, וכך הוא גם ממוין
SUBSTITUTES = func.coalesce(
    db.select(_SUBSTITUTES_PER_PART.c.parts)
    .where(_SUBSTITUTES_PER_PART.c.part_id == Part.id)
    .correlate(Part)
    .scalar_subquery(),
    0,
)

# ---------- חשיבות החלק: כמה רכבים כאלה על הכביש ----------
#
# המספרים באים ממסך הצי, וההצלבה בין דגם במרשם לדגם בקטלוג היא לוגיקה
# של פייתון (נרמול יצרן והכלת שם דגם) - אי אפשר לכתוב אותה ב-SQL בלי
# לשנות אותה, ומספר שסותר את מסך הצי גרוע ממספר שחסר.
#
# מה שכן אפשר: הקטלוג כולו מחזיק כמה עשרות צמדי רכב בלבד. ההצלבה
# נעשית בפייתון פעם אחת, והתוצאה - טבלה קטנה - נכתבת לתוך השאילתה
# כ-CASE. כך המיון והסינון רצים בבסיס הנתונים, על אותם מספרים בדיוק
# שהתא מציג.
#
# הביטויים האלה נבנים בכל בקשה ולא פעם אחת בטעינת המודול, כי הם תלויים
# בצילום הצי הנוכחי. לכן sort_by של העמודות האלה הוא פונקציה.

def fleet_value(field):
    """ביטוי SQL: המספר מהצי של הרכב שהחלק מתאים לו.

    לחלק שמתאים לכמה רכבים - הגדול מביניהם, כלומר הרכב הנפוץ ביותר.
    סכום היה סופר פעמיים דגם שנספר לשני שמות בקטלוג.
    """
    from . import fleet_stats

    numbers = fleet_stats.catalog_fleet_numbers()
    branches = [
        (db.and_(Fitment.make == make, Fitment.model == model), values[field])
        for (make, model), values in numbers.items()
        if values[field]
    ]
    if not branches:
        # אין צילום צי, או שאף רכב בקטלוג אינו במרשם. ה-cast אינו
        # קישוט: ב-Postgres מספר חשוף ב-ORDER BY הוא מספר סידורי של
        # עמודה, ו-"ORDER BY 0" נפסל. עטוף ב-cast הוא שוב ביטוי.
        return cast(db.literal(0), db.Integer)
    return func.coalesce(
        db.select(func.max(case(*branches, else_=0)))
        .where(Fitment.part_id == Part.id)
        .correlate(Part)
        .scalar_subquery(),
        0,
    )


# ---------- הקבוצה: המק"ט וכל התחליפים שלו יחד ----------
#
# טבלת המכירה שואלת שאלה אחרת מטבלת הקטלוג. שם השורה היא מק"ט; כאן
# השורה היא מק"ט *ומה שאפשר להציע במקומו*. המלאי והמחיר בטבלה הזאת
# הם של הקבוצה כולה, כי מוכר שנשאל "יש לך?" לא מוגבל למק"ט אחד.
#
# חברות בקבוצה נקבעת באותו כלל בדיוק כמו עמודת "תחליפים" ורשימת
# המק"טים השקולים בכרטיס - מספר מקביל משותף - ובתוספת המק"ט עצמו.

_GROUP_MEMBERS = union(
    db.select(Part.id.label("part_id"), Part.id.label("member_id")),
    db.select(
        _MY_NUMBERS.c.part_id.label("part_id"),
        CrossReference.part_id.label("member_id"),
    )
    .select_from(_MY_NUMBERS)
    .join(
        CrossReference,
        db.and_(
            func.lower(CrossReference.ref_number) == _MY_NUMBERS.c.ref,
            CrossReference.part_id != _MY_NUMBERS.c.part_id,
        ),
    ),
).subquery()


def _current_org():
    """הארגון של הבקשה הנוכחית.

    המלאי והמחיר יושבים בשכבה הפרטית, ולכן הביטויים האלה נבנים לכל
    בקשה ולא פעם אחת בטעינת המודול - כמו עמודות הצי.
    """
    from .services import current_org_id

    return current_org_id()


def _group_aggregate(value, combine, organization_id=None):
    """ביטוי על כל חברי הקבוצה: סכום מלאי, מחיר הזול ביותר וכדומה.

    הארגון מגיע במפורש כשהקורא יודע אותו, ומהבקשה כשלא - כי המיון
    והסינון נבנים מתוך רישום העמודות ואין להם דרך להעביר אותו.
    """
    if organization_id is None:
        organization_id = _current_org()
    if not organization_id:
        # בלי ארגון אין שכבה פרטית. cast ולא אפס חשוף: ב-Postgres
        # "ORDER BY 0" הוא מספר סידורי של עמודה ולא הערך אפס.
        return cast(db.literal(0), db.Integer)
    grouped = (
        db.select(
            _GROUP_MEMBERS.c.part_id.label("part_id"),
            combine(value).label("value"),
        )
        .select_from(_GROUP_MEMBERS)
        .join(
            OrgPart,
            db.and_(
                OrgPart.part_id == _GROUP_MEMBERS.c.member_id,
                OrgPart.organization_id == organization_id,
            ),
        )
        .group_by(_GROUP_MEMBERS.c.part_id)
        .subquery()
    )
    return func.coalesce(
        db.select(grouped.c.value)
        .where(grouped.c.part_id == Part.id)
        .correlate(Part)
        .scalar_subquery(),
        0,
    )


def group_stock(organization_id=None):
    """כמה יחידות יש בסך הכול - של המק"ט הזה ושל כל תחליפיו."""
    return _group_aggregate(func.coalesce(OrgPart.stock_qty, 0), func.sum, organization_id)


def group_cheapest(organization_id=None):
    """המחיר הזול ביותר שאפשר להציע מהקבוצה, כולל מע"מ."""
    return _group_aggregate(PRICE_WITH_VAT, func.min, organization_id)


def group_dearest(organization_id=None):
    """הקצה השני של הטווח. שני המספרים יחד הם "בין כמה לכמה"."""
    return _group_aggregate(PRICE_WITH_VAT, func.max, organization_id)


def _image_condition(raw):
    """סינון לפי קיום תמונה. שימושי בעיקר כדי למצוא את מה שחסר."""
    wanted = (raw or "").strip()
    if wanted not in ("1", "0"):
        return None
    has_image = db.and_(Part.image_url.isnot(None), Part.image_url != "")
    return has_image if wanted == "1" else db.not_(has_image)

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
               של ערכים, כמו ההתאמות, אין לה סדר אחד נכון). פונקציה =
               ביטוי שנבנה לכל בקשה, כי הוא תלוי בנתונים שמשתנים
               (עמודות הצי נשענות על צילום המרשם הנוכחי)
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
        "vehicle_make", "יצרן רכב",
        sort_by=db.select(func.min(Fitment.make))
        .where(Fitment.part_id == Part.id).correlate(Part).scalar_subquery(),
        param="f_vehicle_make", kind="text",
        apply=lambda raw: _in_related(
            Fitment, Fitment.make.ilike(f"%{raw.strip()}%")
        ),
        text=lambda part, op: _vehicle_names(part, "make"),
    ),
    Column(
        "vehicle_model", "דגם רכב",
        sort_by=db.select(func.min(Fitment.model))
        .where(Fitment.part_id == Part.id).correlate(Part).scalar_subquery(),
        param="f_vehicle_model", kind="text",
        apply=lambda raw: _in_related(
            Fitment, Fitment.model.ilike(f"%{raw.strip()}%")
        ),
        text=lambda part, op: _vehicle_names(part, "model"),
    ),
    # שני אלה יורדים לרזולוציה שמתחת לדגם: אותה קורולה עם מנוע אחר
    # לוקחת חלק אחר. הם קוראים את אותן שורות התאמה, ולכן מק"ט שמתאים
    # לכמה רכבים מציג את כולם - כמו יצרן ודגם.
    Column(
        "engine", "מנוע",
        sort_by=db.select(func.min(Fitment.engine_code))
        .where(Fitment.part_id == Part.id).correlate(Part).scalar_subquery(),
        param="f_engine", kind="text",
        apply=lambda raw: _in_related(
            Fitment, Fitment.engine_code.ilike(f"%{raw.strip()}%")
        ),
        text=lambda part, op: _vehicle_names(part, "engine_code"),
        hint="1.6 GDI",
    ),
    Column(
        "trim", "גימור",
        sort_by=db.select(func.min(Fitment.submodel))
        .where(Fitment.part_id == Part.id).correlate(Part).scalar_subquery(),
        param="f_trim", kind="text",
        apply=lambda raw: _in_related(
            Fitment, Fitment.submodel.ilike(f"%{raw.strip()}%")
        ),
        text=lambda part, op: _vehicle_names(part, "submodel"),
    ),
    Column(
        "catalog_parts", "חלפים במאגר",
        sort_by=CATALOG_PARTS, param="f_catalog_parts", kind="number",
        apply=lambda raw: number_condition(CATALOG_PARTS, raw),
        align="text-end",
        hint=">10",
    ),
    Column(
        "substitutes", "תחליפים",
        sort_by=SUBSTITUTES, param="f_substitutes", kind="number",
        apply=lambda raw: number_condition(SUBSTITUTES, raw),
        align="text-end",
        hint=">0",
    ),
    Column(
        "fleet_vehicles", "רכבים על הכביש",
        sort_by=lambda: fleet_value("vehicles"),
        param="f_fleet_vehicles", kind="number",
        apply=lambda raw: number_condition(fleet_value("vehicles"), raw),
        align="text-end", hint=">10000",
    ),
    Column(
        "fleet_prime", "בטווח הקנייה",
        sort_by=lambda: fleet_value("prime"),
        param="f_fleet_prime", kind="number",
        apply=lambda raw: number_condition(fleet_value("prime"), raw),
        align="text-end", hint=">5000",
    ),
    Column(
        "fleet_gap", 'רכבים למק"ט',
        sort_by=lambda: fleet_value("gap"),
        param="f_fleet_gap", kind="number",
        apply=lambda raw: number_condition(fleet_value("gap"), raw),
        align="text-end", hint=">100",
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

    # ---------- עמודות טבלת המכירה ----------
    #
    # שש עמודות שכל אחת מהן מאחדת כמה נתונים לתא אחד. הן קיימות באותו
    # רישום ולא בנפרד, כי המיון והסינון עובדים על הרישום הזה - ומי
    # שרוצה אחת מהן גם בטבלת הקטלוג יכול פשוט להוסיף אותה.
    Column(
        "vehicle", "רכב",
        sort_by=db.select(func.min(Fitment.make))
        .where(Fitment.part_id == Part.id).correlate(Part).scalar_subquery(),
        param="f_vehicle", kind="text",
        apply=lambda raw: _in_related(
            Fitment,
            db.or_(
                Fitment.make.ilike(f"%{raw.strip()}%"),
                Fitment.model.ilike(f"%{raw.strip()}%"),
                Fitment.engine_code.ilike(f"%{raw.strip()}%"),
                Fitment.fuel.ilike(f"%{raw.strip()}%"),
            ),
        ),
        hint="קורולה, 1.6, בנזין",
    ),
    Column(
        "part_name", "חלק",
        sort_by=Part.name_he, param="f_part_name", kind="text",
        apply=lambda raw: db.or_(
            Part.name_he.ilike(f"%{raw.strip()}%"),
            Part.side.ilike(f"%{raw.strip()}%"),
        ),
        hint="בולם, קדמי ימין",
    ),
    Column(
        "oe_all", 'מק"ט OE',
        # רשימה, ולכן אין לה סדר אחד נכון - אבל מחפשים בה
        param="f_oe_all", kind="text",
        apply=lambda raw: _in_related(
            CrossReference,
            db.and_(
                CrossReference.ref_type == "OEM",
                CrossReference.ref_number.ilike(f"%{raw.strip()}%"),
            ),
        ),
    ),
    Column(
        "image", "תמונה",
        sort_by=Part.image_url, param="f_image", kind="select",
        apply=_image_condition,
        options=lambda: [("1", "יש תמונה"), ("0", "אין תמונה")],
    ),
    Column(
        "demand", "ביקוש",
        # ממוין לפי מי שבטווח הקנייה ולא לפי הצי כולו: אלה הקונים
        sort_by=lambda: fleet_value("prime"),
        param="f_demand", kind="number",
        apply=lambda raw: number_condition(fleet_value("prime"), raw),
        align="text-end", hint=">5000",
    ),
    Column(
        "group_stock", "מלאי בקבוצה",
        sort_by=group_stock, param="f_group_stock", kind="number",
        apply=lambda raw: number_condition(group_stock(), raw),
        needs_org=False, align="text-end", hint=">0",
    ),
    Column(
        "group_price", "מחיר", 
        # ממוין לפי הזול ביותר בקבוצה - זה המספר שמעניין מי שנשאל
        # "כמה זה יוצא לי", והוא גם אחד מהמספרים שהתא מציג
        sort_by=group_cheapest, param="f_group_price", kind="number",
        apply=lambda raw: number_condition(group_cheapest(), raw),
        needs_org=False, align="text-end", hint="<200",
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

# טבלת המכירה: שמונה תאים שכל אחד עונה על שאלה שנשאלת מעבר לדלפק -
# לאיזה רכב, איזה חלק, מה המספר המקורי, איך זה נראה, כמה כאלה על
# הכביש, כמה חלופות אני מכיר, כמה יש לי, וכמה זה עולה.
SALES_KEYS = (
    "vehicle", "part_name", "oe_all", "image",
    "demand", "substitutes", "group_stock", "group_price",
)


def _vehicle_names(part, field):
    """יצרני הרכב או הדגמים שהחלק מתאים להם, בלי כפילויות."""
    seen = []
    for fitment in part.fitments:
        value = getattr(fitment, field)
        if value and value not in seen:
            seen.append(value)
    if not seen:
        return "—"
    return " · ".join(seen[:2]) + (f" ועוד {len(seen) - 2}" if len(seen) > 2 else "")


def _type_name(key):
    from .taxonomy import type_name

    return type_name(key) if key else "—"


def _part_type_options():
    from .taxonomy import all_types

    return [(entry["key"], entry["name_he"]) for entry in all_types()]


def by_key(key):
    return BY_KEY.get(key)


def resolve(keys, fallback=DEFAULT_KEYS):
    """רשימת מפתחות -> עמודות. מפתח שאינו מוכר נופל בשקט.

    פריסה שמורה עשויה להצביע על עמודה שהוסרה מהקוד; טבלה שנשברת
    בגלל זה גרועה מטבלה שחסרה בה עמודה. fallback הוא ברירת המחדל של
    הטבלה המבקשת - לכל טבלה יש משלה.
    """
    columns = [BY_KEY[key] for key in keys if key in BY_KEY]
    return columns or [BY_KEY[key] for key in fallback]


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
    expression = column.sort_by() if callable(column.sort_by) else column.sort_by
    order = expression.desc() if direction == "desc" else expression.asc()
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
