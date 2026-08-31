"""שליפה חיה לפי מספר שלדה

Revision ID: c9d2e5a71b40
Revises: b7e3f2a94c15
Create Date: 2026-08-31 23:10:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c9d2e5a71b40'
down_revision = 'b7e3f2a94c15'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "lookup_cache",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("vin_key", sa.String(length=120), nullable=False),
        sa.Column("part_type", sa.String(length=60), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("sources", sa.String(length=120), nullable=True),
        sa.Column("hits", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("vin_key", "part_type", name="uq_lookup_cache_key"),
    )
    with op.batch_alter_table("lookup_cache", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_lookup_cache_vin_key"), ["vin_key"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_lookup_cache_part_type"), ["part_type"], unique=False
        )

    op.create_table(
        "lookup_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("plate", sa.String(length=20), nullable=True),
        sa.Column("vin_key", sa.String(length=120), nullable=True),
        sa.Column("part_type", sa.String(length=60), nullable=False),
        sa.Column("vehicle", sa.Text(), nullable=False),
        sa.Column("stages", sa.Text(), nullable=False),
        sa.Column("cursor", sa.Integer(), nullable=False),
        sa.Column("results", sa.Text(), nullable=True),
        sa.Column("saved", sa.Integer(), nullable=False),
        sa.Column("log", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("started_by_id", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["started_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("lookup_jobs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_lookup_jobs_status"), ["status"], unique=False)
        batch_op.create_index(batch_op.f("ix_lookup_jobs_plate"), ["plate"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_lookup_jobs_vin_key"), ["vin_key"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_lookup_jobs_organization_id"), ["organization_id"], unique=False
        )

    # התאמה שנולדה מחיפוש לפי שלדה מדויקת לווריאנט, לא לדגם
    with op.batch_alter_table("fitments", schema=None) as batch_op:
        batch_op.add_column(sa.Column("variant_key", sa.String(length=80), nullable=True))


def downgrade():
    with op.batch_alter_table("fitments", schema=None) as batch_op:
        batch_op.drop_column("variant_key")

    with op.batch_alter_table("lookup_jobs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_lookup_jobs_organization_id"))
        batch_op.drop_index(batch_op.f("ix_lookup_jobs_vin_key"))
        batch_op.drop_index(batch_op.f("ix_lookup_jobs_plate"))
        batch_op.drop_index(batch_op.f("ix_lookup_jobs_status"))
    op.drop_table("lookup_jobs")

    with op.batch_alter_table("lookup_cache", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_lookup_cache_part_type"))
        batch_op.drop_index(batch_op.f("ix_lookup_cache_vin_key"))
    op.drop_table("lookup_cache")
