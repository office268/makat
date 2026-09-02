"""צי זריעה קבוע

Revision ID: f6a49c83b127
Revises: e5f31a72c0d8
"""
import sqlalchemy as sa
from alembic import op

revision = "f6a49c83b127"
down_revision = "e5f31a72c0d8"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "seed_vehicles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("vin", sa.String(length=32), nullable=False),
        sa.Column("plate", sa.String(length=20)),
        sa.Column("make", sa.String(length=80), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("year", sa.Integer()),
        sa.Column("engine_code", sa.String(length=40)),
        sa.Column("model_code", sa.String(length=60)),
        sa.Column("vehicles", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("prime", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("note", sa.String(length=200)),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("vin"),
    )
    op.create_index("ix_seed_vehicles_position", "seed_vehicles", ["position"])


def downgrade():
    op.drop_index("ix_seed_vehicles_position", table_name="seed_vehicles")
    op.drop_table("seed_vehicles")
