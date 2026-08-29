"""שכבה פרטית: org_parts, וספקים לפי ארגון

Revision ID: 74e40d5ae6ec
Revises: e8b168d830d4
Create Date: 2026-08-29 20:25:29.275601

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '74e40d5ae6ec'
down_revision = 'e8b168d830d4'
branch_labels = None
depends_on = None


def _migrate_existing_data():
    """מעביר מחירים, מלאי וספקים קיימים לארגון, לפני שהעמודות הישנות נמחקות.

    לפני השינוי המחירים ישבו על parts והספקים היו גלובליים. אין להם ארגון,
    ולכן נוצר ארגון קליטה שאליו הכל משויך. בלי השלב הזה, הפיכת
    suppliers.organization_id ל-NOT NULL נכשלת על שורות קיימות, והמחירים
    שהיו על parts נמחקים ללא גיבוי.
    """
    conn = op.get_bind()

    has_suppliers = conn.execute(
        sa.text("SELECT COUNT(*) FROM suppliers")
    ).scalar()
    has_priced_parts = conn.execute(
        sa.text("SELECT COUNT(*) FROM parts WHERE price IS NOT NULL AND price > 0")
    ).scalar()
    if not has_suppliers and not has_priced_parts:
        return None

    org_id = conn.execute(
        sa.text("SELECT id FROM organizations WHERE slug = 'legacy' LIMIT 1")
    ).scalar()
    if org_id is None:
        conn.execute(
            sa.text(
                "INSERT INTO organizations (name, slug, kind, is_active) "
                "VALUES (:name, 'legacy', 'מוסך', true)"
            ),
            {"name": "נתונים מלפני ההפרדה"},
        )
        org_id = conn.execute(
            sa.text("SELECT id FROM organizations WHERE slug = 'legacy'")
        ).scalar()

    conn.execute(
        sa.text(
            "INSERT INTO org_parts "
            "(organization_id, part_id, price, cost, currency, vat_included, "
            " stock_qty, min_stock, location) "
            "SELECT :org, id, price, cost, currency, vat_included, "
            "       stock_qty, min_stock, location "
            "FROM parts"
        ),
        {"org": org_id},
    )
    conn.execute(
        sa.text("UPDATE suppliers SET organization_id = :org WHERE organization_id IS NULL"),
        {"org": org_id},
    )
    return org_id


def upgrade():
    # הסדר חשוב: קודם יוצרים את היעדים, אחר כך מעבירים את הנתונים,
    # ורק בסוף מוחקים את המקור ומהדקים את האילוצים.

    # 1. טבלת השכבה הפרטית
    op.create_table(
        "org_parts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("part_id", sa.Integer(), nullable=False),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("cost", sa.Float(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("vat_included", sa.Boolean(), nullable=True),
        sa.Column("stock_qty", sa.Integer(), nullable=True),
        sa.Column("min_stock", sa.Integer(), nullable=True),
        sa.Column("location", sa.String(length=80), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["part_id"], ["parts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "part_id", name="uq_org_part"),
    )
    with op.batch_alter_table("org_parts", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_org_parts_organization_id"), ["organization_id"])
        batch_op.create_index(batch_op.f("ix_org_parts_part_id"), ["part_id"])

    # 2. העמודה על suppliers - nullable, כדי שהעברת הנתונים תוכל למלא אותה.
    #    האינדקס על name היה ייחודי ונעשה רגיל, כי הייחודיות עברה לאילוץ
    #    המשולב עם הארגון. בודקים שהוא קיים לפני שמוחקים - הסכימה בפרודקשן
    #    נוצרה ב-create_all() ולא על ידי מיגרציה, ולכן אי אפשר להניח את מבנה.
    existing_indexes = {
        index["name"] for index in sa.inspect(op.get_bind()).get_indexes("suppliers")
    }
    with op.batch_alter_table("suppliers", schema=None) as batch_op:
        batch_op.add_column(sa.Column("organization_id", sa.Integer(), nullable=True))
        if "ix_suppliers_name" in existing_indexes:
            batch_op.drop_index(batch_op.f("ix_suppliers_name"))
        batch_op.create_index(batch_op.f("ix_suppliers_name"), ["name"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_suppliers_organization_id"), ["organization_id"]
        )

    # 3. העברת הנתונים הקיימים - חייב לרוץ אחרי שהיעדים קיימים
    _migrate_existing_data()

    # 4. עכשיו אפשר להדק ולמחוק את המקור
    with op.batch_alter_table("suppliers", schema=None) as batch_op:
        batch_op.alter_column(
            "organization_id", existing_type=sa.Integer(), nullable=False
        )
        batch_op.create_unique_constraint("uq_supplier_org_name", ["organization_id", "name"])
        batch_op.create_foreign_key(
            "fk_suppliers_organization", "organizations", ["organization_id"], ["id"]
        )

    with op.batch_alter_table("parts", schema=None) as batch_op:
        for column in ("price", "cost", "currency", "vat_included",
                       "stock_qty", "min_stock", "location"):
            batch_op.drop_column(column)


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('suppliers', schema=None) as batch_op:
        batch_op.drop_constraint(None, type_='foreignkey')
        batch_op.drop_constraint('uq_supplier_org_name', type_='unique')
        batch_op.drop_index(batch_op.f('ix_suppliers_organization_id'))
        batch_op.drop_index(batch_op.f('ix_suppliers_name'))
        batch_op.create_index(batch_op.f('ix_suppliers_name'), ['name'], unique=1)
        batch_op.drop_column('organization_id')

    with op.batch_alter_table('parts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('cost', sa.FLOAT(), nullable=True))
        batch_op.add_column(sa.Column('min_stock', sa.INTEGER(), nullable=True))
        batch_op.add_column(sa.Column('location', sa.VARCHAR(length=80), nullable=True))
        batch_op.add_column(sa.Column('price', sa.FLOAT(), nullable=True))
        batch_op.add_column(sa.Column('currency', sa.VARCHAR(length=3), nullable=True))
        batch_op.add_column(sa.Column('stock_qty', sa.INTEGER(), nullable=True))
        batch_op.add_column(sa.Column('vat_included', sa.BOOLEAN(), nullable=True))

    with op.batch_alter_table('org_parts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_org_parts_part_id'))
        batch_op.drop_index(batch_op.f('ix_org_parts_organization_id'))

    op.drop_table('org_parts')
    # ### end Alembic commands ###
