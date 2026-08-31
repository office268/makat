"""מודלים של בסיס הנתונים - קטלוג מק"טים לחלקי רכב."""
from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import UniqueConstraint

db = SQLAlchemy()


def _now():
    return datetime.now(timezone.utc)


class Manufacturer(db.Model):
    """יצרן חלקים (בוש, מאהלה, פבי וכו')."""

    __tablename__ = "manufacturers"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True, index=True)
    country = db.Column(db.String(80))
    website = db.Column(db.String(255))
    notes = db.Column(db.Text)

    parts = db.relationship("Part", back_populates="manufacturer")

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "country": self.country,
            "website": self.website,
            "parts_count": len(self.parts),
        }

    def __repr__(self):
        return f"<Manufacturer {self.name}>"


class Category(db.Model):
    """קטגוריית חלקים (מנוע, בלמים, מתלים...) עם תמיכה בתת-קטגוריות."""

    __tablename__ = "categories"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, index=True)
    parent_id = db.Column(db.Integer, db.ForeignKey("categories.id"))

    parent = db.relationship("Category", remote_side=[id], back_populates="children")
    children = db.relationship("Category", back_populates="parent")
    parts = db.relationship("Part", back_populates="category")

    __table_args__ = (UniqueConstraint("name", "parent_id", name="uq_category_name_parent"),)

    @property
    def full_name(self):
        """שם מלא כולל קטגוריית האב."""
        if self.parent:
            return f"{self.parent.name} / {self.name}"
        return self.name

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "full_name": self.full_name,
            "parent_id": self.parent_id,
            "parts_count": len(self.parts),
        }

    def __repr__(self):
        return f"<Category {self.full_name}>"


class Part(db.Model):
    """מק"ט - חלק חילוף לרכב."""

    __tablename__ = "parts"

    id = db.Column(db.Integer, primary_key=True)
    part_number = db.Column(db.String(80), nullable=False, unique=True, index=True)
    name_he = db.Column(db.String(200), nullable=False, index=True)
    name_en = db.Column(db.String(200), index=True)
    description = db.Column(db.Text)

    manufacturer_id = db.Column(db.Integer, db.ForeignKey("manufacturers.id"), index=True)
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), index=True)
    # מפתח מתוך app.taxonomy - מקשר בין תוצאת הזיהוי לבין המק"ט
    part_type = db.Column(db.String(60), index=True)

    barcode = db.Column(db.String(64), index=True)

    weight_kg = db.Column(db.Float)
    dimensions = db.Column(db.String(80))  # אורך x רוחב x גובה בס"מ
    warranty_months = db.Column(db.Integer)
    side = db.Column(db.String(40))  # ימין / שמאל / קדמי / אחורי
    image_url = db.Column(db.String(500))
    notes = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True, index=True)

    created_at = db.Column(db.DateTime, default=_now)
    updated_at = db.Column(db.DateTime, default=_now, onupdate=_now)

    manufacturer = db.relationship("Manufacturer", back_populates="parts")
    category = db.relationship("Category", back_populates="parts")
    cross_refs = db.relationship(
        "CrossReference", back_populates="part", cascade="all, delete-orphan"
    )
    fitments = db.relationship("Fitment", back_populates="part", cascade="all, delete-orphan")
    supplier_links = db.relationship(
        "PartSupplier", back_populates="part", cascade="all, delete-orphan"
    )
    org_links = db.relationship(
        "OrgPart", back_populates="part", cascade="all, delete-orphan"
    )

    def for_org(self, organization_id):
        """השכבה הפרטית של ארגון מסוים על המק"ט הזה, אם קיימת."""
        if not organization_id:
            return None
        return next(
            (link for link in self.org_links if link.organization_id == organization_id),
            None,
        )

    @property
    def oem_numbers(self):
        """המק"טים המקוריים (OEM) של החלק, לפי סדר ההזנה."""
        return [ref.ref_number for ref in self.cross_refs if ref.ref_type == "OEM"]

    def to_dict(self, full=False, organization_id=None):
        data = {
            "id": self.id,
            "part_number": self.part_number,
            "name_he": self.name_he,
            "name_en": self.name_en,
            "manufacturer": self.manufacturer.name if self.manufacturer else None,
            "category": self.category.full_name if self.category else None,
            "part_type": self.part_type,
            "is_active": self.is_active,
        }
        # מחיר ומלאי פרטיים לארגון - נחשפים רק למי ששייך אליו
        org_part = self.for_org(organization_id)
        if org_part:
            data.update(
                {
                    "price": org_part.price,
                    "price_with_vat": org_part.price_with_vat,
                    "currency": org_part.currency,
                    "stock_qty": org_part.stock_qty,
                    "in_stock": org_part.in_stock,
                }
            )
        if full:
            data.update(
                {
                    "description": self.description,
                    "barcode": self.barcode,
                    "weight_kg": self.weight_kg,
                    "dimensions": self.dimensions,
                    "warranty_months": self.warranty_months,
                    "side": self.side,
                    "image_url": self.image_url,
                    "notes": self.notes,
                    "cross_refs": [ref.to_dict() for ref in self.cross_refs],
                    "fitments": [fit.to_dict() for fit in self.fitments],
                    "created_at": self.created_at.isoformat() if self.created_at else None,
                    "updated_at": self.updated_at.isoformat() if self.updated_at else None,
                }
            )
            if org_part:
                data.update(
                    {
                        "cost": org_part.cost,
                        "margin_percent": org_part.margin_percent,
                        "vat_included": org_part.vat_included,
                        "min_stock": org_part.min_stock,
                        "low_stock": org_part.low_stock,
                        "location": org_part.location,
                        "suppliers": [
                            link.to_dict()
                            for link in self.supplier_links
                            if link.supplier
                            and link.supplier.organization_id == organization_id
                        ],
                    }
                )
        return data

    def __repr__(self):
        return f"<Part {self.part_number}>"


class CrossReference(db.Model):
    """מק"ט מקביל - מקורי (OEM), חלופי או של יצרן אחר."""

    __tablename__ = "cross_references"

    REF_TYPES = ("OEM", "חלופי", "יצרן אחר")

    id = db.Column(db.Integer, primary_key=True)
    part_id = db.Column(db.Integer, db.ForeignKey("parts.id"), nullable=False, index=True)
    ref_number = db.Column(db.String(80), nullable=False, index=True)
    ref_type = db.Column(db.String(40), default="OEM")
    ref_brand = db.Column(db.String(120))  # היצרן שאליו שייך המק"ט המקביל
    notes = db.Column(db.String(255))

    part = db.relationship("Part", back_populates="cross_refs")

    __table_args__ = (
        UniqueConstraint("part_id", "ref_number", "ref_brand", name="uq_crossref"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "ref_number": self.ref_number,
            "ref_type": self.ref_type,
            "ref_brand": self.ref_brand,
            "notes": self.notes,
        }

    def __repr__(self):
        return f"<CrossReference {self.ref_number}>"


class Fitment(db.Model):
    """התאמה לרכב - לאיזה יצרן/דגם/שנים/מנוע החלק מתאים."""

    __tablename__ = "fitments"

    id = db.Column(db.Integer, primary_key=True)
    part_id = db.Column(db.Integer, db.ForeignKey("parts.id"), nullable=False, index=True)
    make = db.Column(db.String(80), nullable=False, index=True)  # יצרן הרכב
    model = db.Column(db.String(120), index=True)  # דגם
    submodel = db.Column(db.String(120))  # גרסה / רמת גימור
    engine_code = db.Column(db.String(60))  # קוד מנוע
    engine_volume = db.Column(db.String(20))  # נפח מנוע
    fuel = db.Column(db.String(40))  # בנזין / דיזל / היברידי / חשמלי
    year_from = db.Column(db.Integer, index=True)
    year_to = db.Column(db.Integer, index=True)
    notes = db.Column(db.String(255))

    part = db.relationship("Part", back_populates="fitments")

    @property
    def years(self):
        if self.year_from and self.year_to:
            return f"{self.year_from}-{self.year_to}"
        if self.year_from:
            return f"{self.year_from}+"
        if self.year_to:
            return f"עד {self.year_to}"
        return "כל השנים"

    def matches_year(self, year):
        if year is None:
            return True
        if self.year_from and year < self.year_from:
            return False
        if self.year_to and year > self.year_to:
            return False
        return True

    def to_dict(self):
        return {
            "id": self.id,
            "make": self.make,
            "model": self.model,
            "submodel": self.submodel,
            "engine_code": self.engine_code,
            "engine_volume": self.engine_volume,
            "fuel": self.fuel,
            "year_from": self.year_from,
            "year_to": self.year_to,
            "years": self.years,
            "notes": self.notes,
        }

    def __repr__(self):
        return f"<Fitment {self.make} {self.model} {self.years}>"


class Supplier(db.Model):
    """ספק."""

    __tablename__ = "suppliers"

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(
        db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True
    )
    name = db.Column(db.String(120), nullable=False, index=True)
    contact_name = db.Column(db.String(120))
    phone = db.Column(db.String(40))
    email = db.Column(db.String(120))
    address = db.Column(db.String(255))
    notes = db.Column(db.Text)

    part_links = db.relationship(
        "PartSupplier", back_populates="supplier", cascade="all, delete-orphan"
    )
    organization = db.relationship("Organization")

    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_supplier_org_name"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "contact_name": self.contact_name,
            "phone": self.phone,
            "email": self.email,
            "address": self.address,
            "organization_id": self.organization_id,
            "parts_count": len(self.part_links),
        }

    def __repr__(self):
        return f"<Supplier {self.name}>"


class PartSupplier(db.Model):
    """קישור בין מק"ט לספק - מחיר קנייה, מק"ט הספק וזמן אספקה."""

    __tablename__ = "part_suppliers"

    id = db.Column(db.Integer, primary_key=True)
    part_id = db.Column(db.Integer, db.ForeignKey("parts.id"), nullable=False, index=True)
    supplier_id = db.Column(db.Integer, db.ForeignKey("suppliers.id"), nullable=False, index=True)
    supplier_sku = db.Column(db.String(80))  # המק"ט אצל הספק
    cost = db.Column(db.Float)
    lead_time_days = db.Column(db.Integer)
    is_preferred = db.Column(db.Boolean, default=False)

    part = db.relationship("Part", back_populates="supplier_links")
    supplier = db.relationship("Supplier", back_populates="part_links")

    __table_args__ = (UniqueConstraint("part_id", "supplier_id", name="uq_part_supplier"),)

    def to_dict(self):
        return {
            "id": self.id,
            "supplier_id": self.supplier_id,
            "supplier": self.supplier.name if self.supplier else None,
            "supplier_sku": self.supplier_sku,
            "cost": self.cost,
            "lead_time_days": self.lead_time_days,
            "is_preferred": self.is_preferred,
        }

    def __repr__(self):
        return f"<PartSupplier part={self.part_id} supplier={self.supplier_id}>"


class OrgPart(db.Model):
    """השכבה הפרטית: מה שארגון מסוים יודע על מק"ט מהקטלוג המשותף.

    הקטלוג (Part) משותף לכולם - מספר, שם, יצרן, התאמות לרכב. מה שמסחרי
    ורגיש - מחיר, עלות, מלאי ומיקום במדף - יושב כאן, ונחשף רק לארגון שלו.
    """

    __tablename__ = "org_parts"

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(
        db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True
    )
    part_id = db.Column(db.Integer, db.ForeignKey("parts.id"), nullable=False, index=True)

    price = db.Column(db.Float, default=0.0)
    cost = db.Column(db.Float, default=0.0)
    currency = db.Column(db.String(3), default="ILS")
    vat_included = db.Column(db.Boolean, default=False)

    stock_qty = db.Column(db.Integer, default=0)
    min_stock = db.Column(db.Integer, default=0)
    location = db.Column(db.String(80))  # מיקום במחסן
    notes = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=_now)
    updated_at = db.Column(db.DateTime, default=_now, onupdate=_now)

    part = db.relationship("Part", back_populates="org_links")
    organization = db.relationship("Organization")

    __table_args__ = (
        UniqueConstraint("organization_id", "part_id", name="uq_org_part"),
    )

    @property
    def in_stock(self):
        return (self.stock_qty or 0) > 0

    @property
    def low_stock(self):
        return (self.stock_qty or 0) <= (self.min_stock or 0)

    @property
    def price_with_vat(self):
        """מחיר כולל מע"מ (18%)."""
        if self.price is None:
            return None
        if self.vat_included:
            return round(self.price, 2)
        return round(self.price * 1.18, 2)

    @property
    def margin_percent(self):
        """אחוז רווח גולמי מול מחיר העלות."""
        if not self.price or not self.cost:
            return None
        return round((self.price - self.cost) / self.price * 100, 1)

    def to_dict(self):
        return {
            "id": self.id,
            "part_id": self.part_id,
            "organization_id": self.organization_id,
            "price": self.price,
            "price_with_vat": self.price_with_vat,
            "cost": self.cost,
            "margin_percent": self.margin_percent,
            "currency": self.currency,
            "vat_included": self.vat_included,
            "stock_qty": self.stock_qty,
            "min_stock": self.min_stock,
            "in_stock": self.in_stock,
            "low_stock": self.low_stock,
            "location": self.location,
            "notes": self.notes,
        }

    def __repr__(self):
        return f"<OrgPart org={self.organization_id} part={self.part_id}>"


class TableLayout(db.Model):
    """אילו עמודות מוצגות בטבלה, ובאיזה סדר.

    שורה אחת לכל טבלה, לכל המערכת. הפריסה אינה העדפה של משתמש אלא
    החלטה של מנהל האפליקציה מה נכון להראות - ולכן היא לא נשמרת לפי
    משתמש ולא לפי ארגון, ומי שמשנה אותה משנה אותה לכולם.

    הסדר נשמר כרשימת מפתחות ולא כמספרי מיקום: מיקומים היו צריכים
    סידור מחדש בכל הזזה, ורשימה פשוט אומרת מה בא אחרי מה. המפתחות
    עצמם מוגדרים ב-app/part_columns.py.
    """

    __tablename__ = "table_layouts"

    id = db.Column(db.Integer, primary_key=True)
    table_key = db.Column(db.String(40), nullable=False, unique=True, index=True)
    column_keys = db.Column(db.Text, nullable=False)  # JSON: רשימת מפתחות לפי סדר
    updated_at = db.Column(db.DateTime, default=_now, onupdate=_now)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    updated_by = db.relationship("User", foreign_keys=[updated_by_id])

    @property
    def keys(self):
        import json

        try:
            keys = json.loads(self.column_keys or "[]")
        except ValueError:
            return []
        return [key for key in keys if isinstance(key, str)]

    @keys.setter
    def keys(self, values):
        import json

        self.column_keys = json.dumps(list(values), ensure_ascii=False)

    def __repr__(self):
        return f"<TableLayout {self.table_key}>"
