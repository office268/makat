"""פילוח גיל בצילום הצי

Revision ID: e2f4a6c19d73
Revises: c5b8f31d97ae
Create Date: 2026-08-31 07:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e2f4a6c19d73'
down_revision = 'c5b8f31d97ae'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('fleet_model_counts', schema=None) as batch_op:
        # עד 3 שנים, 4-12 (חלון האפטרמרקט), ומעליו
        batch_op.add_column(
            sa.Column('young', sa.Integer(), nullable=False, server_default='0')
        )
        batch_op.add_column(
            sa.Column('prime', sa.Integer(), nullable=False, server_default='0')
        )
        batch_op.add_column(
            sa.Column('old', sa.Integer(), nullable=False, server_default='0')
        )
        # המסך ממיין לפי טווח הקנייה, ולכן זה מיון תדיר כמו לפי סה"כ
        batch_op.create_index(batch_op.f('ix_fleet_model_counts_prime'), ['prime'],
                              unique=False)


def downgrade():
    with op.batch_alter_table('fleet_model_counts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_fleet_model_counts_prime'))
        batch_op.drop_column('old')
        batch_op.drop_column('prime')
        batch_op.drop_column('young')
