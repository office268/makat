"""מונה כשלונות רצופים בייבוא

Revision ID: 8cc82a0a1e45
Revises: 22ce39bc7731
Create Date: 2026-08-29 23:46:17.366401

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '8cc82a0a1e45'
down_revision = '22ce39bc7731'
branch_labels = None
depends_on = None


def upgrade():
    # server_default נדרש: בפרודקשן כבר יש שורות בטבלה, ובלעדיו
    # העמודה NOT NULL לא יכולה להיווצר עליהן
    with op.batch_alter_table("vehicle_import_jobs", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("failures", sa.Integer(), nullable=False, server_default="0")
        )


def downgrade():
    with op.batch_alter_table("vehicle_import_jobs", schema=None) as batch_op:
        batch_op.drop_column("failures")
