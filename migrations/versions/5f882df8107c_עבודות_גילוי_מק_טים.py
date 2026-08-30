"""עבודות גילוי מק"טים

Revision ID: 5f882df8107c
Revises: 8cc82a0a1e45
Create Date: 2026-08-30 03:14:13.057772

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '5f882df8107c'
down_revision = '8cc82a0a1e45'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "discovery_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("targets", sa.Text(), nullable=False),
        sa.Column("cursor", sa.Integer(), nullable=False),
        sa.Column("created", sa.Integer(), nullable=False),
        sa.Column("updated", sa.Integer(), nullable=False),
        sa.Column("rejected", sa.Integer(), nullable=False),
        sa.Column("log", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_by_id", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["started_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("discovery_jobs", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_discovery_jobs_status"), ["status"], unique=False
        )


def downgrade():
    with op.batch_alter_table("discovery_jobs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_discovery_jobs_status"))
    op.drop_table("discovery_jobs")
