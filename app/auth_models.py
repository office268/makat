"""ארגונים ומשתמשים - הבסיס לריבוי לקוחות.

הקטלוג עצמו (Part, Manufacturer, Fitment...) משותף לכל הארגונים.
מה ששייך לארגון בודד - מחירים, מלאי, ספקים - נתלה על Organization.
"""
from datetime import datetime, timezone

from flask import current_app
from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from .models import db


def _now():
    return datetime.now(timezone.utc)


class Organization(db.Model):
    """לקוח של המערכת - מוסך, שמאי או יבואן."""

    __tablename__ = "organizations"

    KINDS = ("מוסך", "שמאי", "יבואן חלפים", "אחר")

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    slug = db.Column(db.String(80), nullable=False, unique=True, index=True)
    kind = db.Column(db.String(40), default="מוסך")
    phone = db.Column(db.String(40))
    address = db.Column(db.String(255))
    tax_id = db.Column(db.String(40))  # ח.פ / ע.מ
    is_active = db.Column(db.Boolean, default=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=_now)

    users = db.relationship(
        "User", back_populates="organization", cascade="all, delete-orphan"
    )
    invitations = db.relationship(
        "Invitation", back_populates="organization", cascade="all, delete-orphan"
    )

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "slug": self.slug,
            "kind": self.kind,
            "is_active": self.is_active,
            "users_count": len(self.users),
        }

    def __repr__(self):
        return f"<Organization {self.slug}>"


class User(UserMixin, db.Model):
    """משתמש. שייך תמיד לארגון אחד."""

    __tablename__ = "users"

    # מסודר מהחזק לחלש - ההשוואה ב-has_role מסתמכת על הסדר
    ROLES = ("owner", "manager", "mechanic")
    ROLE_LABELS = {
        "owner": "בעלים",
        "manager": "מנהל",
        "mechanic": "מכונאי",
    }

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(190), nullable=False, unique=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    full_name = db.Column(db.String(120))
    role = db.Column(db.String(20), default="mechanic", nullable=False)
    organization_id = db.Column(
        db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True
    )
    active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=_now)
    last_login_at = db.Column(db.DateTime)

    organization = db.relationship("Organization", back_populates="users")

    # ---- סיסמה ----
    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw):
        return check_password_hash(self.password_hash, raw or "")

    # ---- הרשאות ----
    def has_role(self, minimum):
        """האם למשתמש יש לפחות את התפקיד הנתון. owner > manager > mechanic."""
        try:
            return self.ROLES.index(self.role) <= self.ROLES.index(minimum)
        except ValueError:
            return False

    @property
    def can_edit_catalog(self):
        """הזנת מק"טים ועדכון מחירים ומלאי."""
        return self.has_role("manager")

    @property
    def can_manage_users(self):
        return self.has_role("owner")

    @property
    def is_superadmin(self):
        """מנהל מערכת - חוצה ארגונים.

        קטלוג דגמי הרכב משותף לכל הארגונים, ולכן ייבוא שלו הוא פעולה ברמת
        המערכת ולא ברמת המוסך. ההרשאה לא נשמרת ב-DB אלא נגזרת מהסביבה,
        כדי שלא תהיה שום דרך להעניק אותה מתוך היישום.
        """
        if not self.active:
            return False
        emails = current_app.config.get("SUPERADMIN_EMAILS") or frozenset()
        return (self.email or "").strip().lower() in emails

    @property
    def role_label(self):
        return self.ROLE_LABELS.get(self.role, self.role)

    @property
    def is_active(self):
        """Flask-Login בודק את זה לפני שהוא מאשר סשן.

        משתמש מושבת *או* ארגון מושבת - שניהם חוסמים כניסה. בלי זה,
        השבתת ארגון (למשל על אי-תשלום) לא הייתה מנתקת את המשתמשים שלו.
        """
        return bool(self.active and self.organization and self.organization.is_active)

    def get_id(self):
        return str(self.id)

    def to_dict(self):
        return {
            "id": self.id,
            "email": self.email,
            "full_name": self.full_name,
            "role": self.role,
            "role_label": self.role_label,
            "organization": self.organization.name if self.organization else None,
            "organization_id": self.organization_id,
            "active": self.active,
            "is_superadmin": self.is_superadmin,
        }

    def __repr__(self):
        return f"<User {self.email} ({self.role})>"


class Invitation(db.Model):
    """הזמנה של עובד לארגון.

    הטוקן הוא הסוד היחיד שמאפשר הצטרפות, ולכן הוא ארוך ואקראי ופג
    אחרי שבועיים. הזמנה שנוצלה נשמרת עם accepted_at - היא כבר לא
    תקפה, אבל היא מתעדת מי צורף ומתי.
    """

    __tablename__ = "invitations"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(190), nullable=False, index=True)
    role = db.Column(db.String(20), default="mechanic", nullable=False)
    token = db.Column(db.String(64), nullable=False, unique=True, index=True)
    organization_id = db.Column(
        db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True
    )
    invited_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=_now)
    expires_at = db.Column(db.DateTime, nullable=False)
    accepted_at = db.Column(db.DateTime)

    organization = db.relationship("Organization", back_populates="invitations")
    invited_by = db.relationship("User", foreign_keys=[invited_by_id])

    @property
    def is_expired(self):
        expires = self.expires_at
        # שורות שנכתבו ב-SQLite חוזרות בלי אזור זמן
        if expires is not None and expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return expires is not None and expires < datetime.now(timezone.utc)

    @property
    def role_label(self):
        return User.ROLE_LABELS.get(self.role, self.role)

    def __repr__(self):
        return f"<Invitation {self.email} -> org {self.organization_id}>"
