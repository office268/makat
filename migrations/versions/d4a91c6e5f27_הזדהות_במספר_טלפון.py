"""הזדהות במספר טלפון

Revision ID: d4a91c6e5f27
Revises: e2f4a6c19d73
Create Date: 2026-08-31 10:00:00.000000

הזהות עוברת מדוא"ל+סיסמה למספר טלפון יחיד:

  users.phone       הזהות החדשה, ייחודית ומאונדקסת
  users.email       הופך לא-חובה (ממנו עדיין נגזרת הרשאת-העל)
  users.password_hash  נמחק - אין סיסמאות, ואין טעם להשאיר גיבובים
  invitations       נמחקת - הזמנה בדוא"ל הייתה דרך לקבוע סיסמה
  activity_log.user_email -> user_label   הלוג רושם את הזהות שנכנסה

ובסוף - שלושת המורשים הראשונים. הם נכתבים לטבלה ולא לקובץ הגדרות
או למשתנה סביבה, כי "מי מורשה להיכנס" הוא נתון שמשתנה: מכאן והלאה
מוסיפים ומשביתים אותם במסך הצוות, בלי פריסה מחדש.
"""
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = 'd4a91c6e5f27'
down_revision = 'e2f4a6c19d73'
branch_labels = None
depends_on = None


# מנהל המערכת הוא משתמש קיים: אותה כתובת דוא"ל שממנה נגזרת הרשאת-העל
# (SUPERADMIN_EMAILS), ועכשיו גם מספר טלפון שאיתו נכנסים.
ADMIN_EMAIL = "office@make-i-tec.com"
AUTHORIZED = (
    # טלפון,        תפקיד,       דוא"ל אם יש
    ("0532798782", "owner", ADMIN_EMAIL),
    ("0527977040", "mechanic", None),
    ("0538294536", "mechanic", None),
)


def _columns(table):
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def _seed_authorized():
    """כותב את המורשים. בטוח להרצה חוזרת - מדלג על מה שכבר קיים."""
    bind = op.get_bind()
    now = datetime.now(timezone.utc)

    organization_id = bind.execute(
        sa.text("SELECT id FROM organizations ORDER BY id LIMIT 1")
    ).scalar()
    if organization_id is None:
        # בסיס נתונים ריק (התקנה חדשה): אין למי לתלות את המשתמשים
        bind.execute(
            sa.text(
                "INSERT INTO organizations (name, slug, kind, is_active, created_at) "
                "VALUES (:name, :slug, :kind, :active, :now)"
            ),
            {"name": "מוסך", "slug": "garage", "kind": "מוסך", "active": True, "now": now},
        )
        organization_id = bind.execute(
            sa.text("SELECT id FROM organizations ORDER BY id LIMIT 1")
        ).scalar()

    for phone, role, email in AUTHORIZED:
        taken = bind.execute(
            sa.text("SELECT id FROM users WHERE phone = :phone"), {"phone": phone}
        ).scalar()
        if taken is not None:
            continue

        existing = (
            bind.execute(
                sa.text("SELECT id FROM users WHERE lower(email) = :email"),
                {"email": email},
            ).scalar()
            if email
            else None
        )
        if existing is not None:
            # המנהל כבר במערכת - רק מצמידים לו את המספר שאיתו ייכנס
            bind.execute(
                sa.text("UPDATE users SET phone = :phone WHERE id = :id"),
                {"phone": phone, "id": existing},
            )
            continue

        bind.execute(
            sa.text(
                "INSERT INTO users (phone, email, role, organization_id, active, created_at) "
                "VALUES (:phone, :email, :role, :organization_id, :active, :now)"
            ),
            {
                "phone": phone,
                "email": email,
                "role": role,
                "organization_id": organization_id,
                "active": True,
                "now": now,
            },
        )


def upgrade():
    # ההזמנות יורדות ראשונות: יש להן מפתח זר ל-users, ובנייה מחדש של
    # users ב-SQLite (batch) מתחת לטבלה שמצביעה עליה משאירה הפניה שבורה.
    inspector = sa.inspect(op.get_bind())
    if "invitations" in inspector.get_table_names():
        op.drop_table("invitations")

    users = _columns("users")
    with op.batch_alter_table("users", schema=None) as batch_op:
        if "phone" not in users:
            batch_op.add_column(sa.Column("phone", sa.String(length=20), nullable=True))
            batch_op.create_index(
                batch_op.f("ix_users_phone"), ["phone"], unique=True
            )
        if "email" in users:
            batch_op.alter_column(
                "email", existing_type=sa.String(length=190), nullable=True
            )
        if "password_hash" in users:
            batch_op.drop_column("password_hash")

    if "user_email" in _columns("activity_log"):
        with op.batch_alter_table("activity_log", schema=None) as batch_op:
            batch_op.alter_column(
                "user_email",
                new_column_name="user_label",
                existing_type=sa.String(length=190),
            )

    _seed_authorized()


def downgrade():
    if "user_label" in _columns("activity_log"):
        with op.batch_alter_table("activity_log", schema=None) as batch_op:
            batch_op.alter_column(
                "user_label",
                new_column_name="user_email",
                existing_type=sa.String(length=190),
            )

    users = _columns("users")
    with op.batch_alter_table("users", schema=None) as batch_op:
        if "password_hash" not in users:
            # הגיבובים עצמם אינם ניתנים לשחזור - החזרה למסלול הסיסמאות
            # מחייבת לקבוע סיסמאות מחדש.
            batch_op.add_column(
                sa.Column("password_hash", sa.String(length=255), nullable=True)
            )
        if "phone" in users:
            batch_op.drop_index(batch_op.f("ix_users_phone"))
            batch_op.drop_column("phone")

    op.create_table(
        "invitations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=190), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("invited_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["invited_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("invitations", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_invitations_email"), ["email"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_invitations_organization_id"), ["organization_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_invitations_token"), ["token"], unique=True)
