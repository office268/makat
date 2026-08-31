"""פריסת עמודות לטבלאות

Revision ID: b7e3f2a94c15
Revises: d4a91c6e5f27
Create Date: 2026-08-31 12:00:00.000000

אילו עמודות מוצגות בטבלה ובאיזה סדר. שורה אחת לכל טבלה, לכל המערכת:
מנהל האפליקציה קובע, וכולם רואים. אין שורה = ברירת המחדל שבקוד
(app/part_columns.py), ולכן הטבלה עובדת גם לפני שנגעו בה.
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = 'b7e3f2a94c15'
down_revision = 'd4a91c6e5f27'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "table_layouts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("table_key", sa.String(length=40), nullable=False),
        sa.Column("column_keys", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("table_layouts", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_table_layouts_table_key"), ["table_key"], unique=True
        )


def downgrade():
    with op.batch_alter_table("table_layouts", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_table_layouts_table_key"))
    op.drop_table("table_layouts")
