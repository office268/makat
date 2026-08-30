"""ספירת רכבים פעילים לפי דגם

Revision ID: f1c93a27d5ba
Revises: b3d71f0a9c42
Create Date: 2026-08-30 20:10:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f1c93a27d5ba'
down_revision = 'b3d71f0a9c42'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'fleet_model_counts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('make', sa.String(length=80), nullable=False),
        sa.Column('model', sa.String(length=120), nullable=False),
        sa.Column('model_code', sa.String(length=60), nullable=True),
        sa.Column('vehicles', sa.Integer(), nullable=False),
        sa.Column('year_from', sa.Integer(), nullable=True),
        sa.Column('year_to', sa.Integer(), nullable=True),
        sa.Column('taken_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('fleet_model_counts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_fleet_model_counts_make'), ['make'], unique=False)
        batch_op.create_index(batch_op.f('ix_fleet_model_counts_model'), ['model'], unique=False)
        batch_op.create_index(batch_op.f('ix_fleet_model_counts_model_code'), ['model_code'], unique=False)
        # המסך מסודר מהדגם הנפוץ לנדיר, ולכן המיון הזה הוא השאילתה הרגילה
        batch_op.create_index(batch_op.f('ix_fleet_model_counts_vehicles'), ['vehicles'], unique=False)


def downgrade():
    with op.batch_alter_table('fleet_model_counts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_fleet_model_counts_vehicles'))
        batch_op.drop_index(batch_op.f('ix_fleet_model_counts_model_code'))
        batch_op.drop_index(batch_op.f('ix_fleet_model_counts_model'))
        batch_op.drop_index(batch_op.f('ix_fleet_model_counts_make'))
    op.drop_table('fleet_model_counts')
