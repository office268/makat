"""הרצות ספירת צי

Revision ID: a7d0e4c81b52
Revises: f1c93a27d5ba
Create Date: 2026-08-30 21:20:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a7d0e4c81b52'
down_revision = 'f1c93a27d5ba'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'fleet_stats_jobs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('offset', sa.Integer(), nullable=False),
        sa.Column('models', sa.Integer(), nullable=False),
        sa.Column('vehicles', sa.Integer(), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('failures', sa.Integer(), nullable=False),
        # החותמת של הצילום שההרצה בונה: כל שורה שהיא כותבת נושאת אותה,
        # ולכן צילום חלקי אינו מתערבב בזה שמוצג
        sa.Column('snapshot_at', sa.DateTime(), nullable=False),
        sa.Column('started_by_id', sa.Integer(), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['started_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('fleet_stats_jobs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_fleet_stats_jobs_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_fleet_stats_jobs_snapshot_at'), ['snapshot_at'], unique=False)


def downgrade():
    with op.batch_alter_table('fleet_stats_jobs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_fleet_stats_jobs_snapshot_at'))
        batch_op.drop_index(batch_op.f('ix_fleet_stats_jobs_status'))
    op.drop_table('fleet_stats_jobs')
