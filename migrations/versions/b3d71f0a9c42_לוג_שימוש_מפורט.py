"""לוג שימוש מפורט

Revision ID: b3d71f0a9c42
Revises: 5f882df8107c
Create Date: 2026-08-30 12:50:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b3d71f0a9c42'
down_revision = '5f882df8107c'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "activity_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("user_email", sa.String(length=190), nullable=True),
        sa.Column("user_role", sa.String(length=20), nullable=True),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("summary", sa.String(length=255), nullable=True),
        sa.Column("entity_type", sa.String(length=40), nullable=True),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("method", sa.String(length=8), nullable=True),
        sa.Column("path", sa.String(length=255), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("ip", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.Column("details", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("activity_log", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_activity_log_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_activity_log_organization_id"),
            ["organization_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_activity_log_user_id"), ["user_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_activity_log_action"), ["action"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_activity_log_status_code"), ["status_code"], unique=False
        )
        # השאילתה של המסך: הארגון שלי, לפי סדר יורד של זמן
        batch_op.create_index(
            "ix_activity_log_org_created",
            ["organization_id", "created_at"],
            unique=False,
        )


def downgrade():
    with op.batch_alter_table("activity_log", schema=None) as batch_op:
        batch_op.drop_index("ix_activity_log_org_created")
        batch_op.drop_index(batch_op.f("ix_activity_log_status_code"))
        batch_op.drop_index(batch_op.f("ix_activity_log_action"))
        batch_op.drop_index(batch_op.f("ix_activity_log_user_id"))
        batch_op.drop_index(batch_op.f("ix_activity_log_organization_id"))
        batch_op.drop_index(batch_op.f("ix_activity_log_created_at"))
    op.drop_table("activity_log")
