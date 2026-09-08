"""קציר מק"טים לפי סבבים

Revision ID: d3b96a10f5c2
Revises: f6a49c83b127
Create Date: 2026-09-08 03:10:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd3b96a10f5c2'
down_revision = 'f6a49c83b127'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "harvest_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("wanted_per_round", sa.Integer(), nullable=False),
        sa.Column("rounds", sa.Integer(), nullable=False),
        sa.Column("round_index", sa.Integer(), nullable=False),
        sa.Column("targets", sa.Text(), nullable=False),
        sa.Column("visited", sa.Text(), nullable=True),
        sa.Column("cursor", sa.Integer(), nullable=False),
        sa.Column("added_round", sa.Integer(), nullable=False),
        sa.Column("added_total", sa.Integer(), nullable=False),
        sa.Column("updated_total", sa.Integer(), nullable=False),
        sa.Column("rejected_total", sa.Integer(), nullable=False),
        sa.Column("fetches", sa.Integer(), nullable=False),
        sa.Column("seen_first", sa.Text(), nullable=True),
        sa.Column("log", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_by_id", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["started_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("harvest_jobs", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_harvest_jobs_status"), ["status"], unique=False
        )


def downgrade():
    with op.batch_alter_table("harvest_jobs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_harvest_jobs_status"))
    op.drop_table("harvest_jobs")
