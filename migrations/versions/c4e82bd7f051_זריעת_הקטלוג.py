"""זריעת הקטלוג

מק"טים שמובאים מראש לרכבים נפוצים, במקום לחכות שמכונאי ישאל.
מטרה אחת בכל פעם, והעבודה זוכרת איפה עצרה.

Revision ID: c4e82bd7f051
Revises: b3d51c7a90e4
"""
import sqlalchemy as sa
from alembic import op

revision = 'c4e82bd7f051'
down_revision = 'b3d51c7a90e4'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'seed_jobs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False,
                  server_default='running'),
        sa.Column('targets', sa.Text(), nullable=False),
        sa.Column('cursor', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('child_id', sa.Integer(), nullable=True),
        sa.Column('found', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('missing', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('failed', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('saved', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_result', sa.Text(), nullable=True),
        sa.Column('log', sa.Text(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('started_by_id', sa.Integer(), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['started_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_seed_jobs_status', 'seed_jobs', ['status'])


def downgrade():
    op.drop_index('ix_seed_jobs_status', table_name='seed_jobs')
    op.drop_table('seed_jobs')
