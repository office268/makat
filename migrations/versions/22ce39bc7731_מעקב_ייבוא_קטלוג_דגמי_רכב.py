"""מעקב ייבוא קטלוג דגמי רכב

Revision ID: 22ce39bc7731
Revises: ca8b0ee2454d
Create Date: 2026-08-29 23:20:20.162790

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '22ce39bc7731'
down_revision = 'ca8b0ee2454d'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "vehicle_import_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("offset", sa.Integer(), nullable=False),
        sa.Column("total", sa.Integer(), nullable=True),
        sa.Column("fetched", sa.Integer(), nullable=False),
        sa.Column("created", sa.Integer(), nullable=False),
        sa.Column("updated", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_by_id", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["started_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("vehicle_import_jobs", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_vehicle_import_jobs_status"), ["status"], unique=False
        )


def downgrade():
    with op.batch_alter_table("vehicle_import_jobs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_vehicle_import_jobs_status"))
    op.drop_table("vehicle_import_jobs")
