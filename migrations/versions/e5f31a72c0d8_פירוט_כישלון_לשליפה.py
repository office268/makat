"""פירוט כישלון לשליפה

היומן אומר מה קרה; העמודה הזו אומרת איפה נעצר. שלבים כ-JSON,
כדי שהמסך יוכל להראות פירוט ולא רק זרם שורות.

Revision ID: e5f31a72c0d8
Revises: c4e82bd7f051
"""
import sqlalchemy as sa
from alembic import op

revision = 'e5f31a72c0d8'
down_revision = 'c4e82bd7f051'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('lookup_jobs') as batch:
        batch.add_column(sa.Column('diagnosis', sa.Text(), nullable=True,
                                   server_default=''))


def downgrade():
    with op.batch_alter_table('lookup_jobs') as batch:
        batch.drop_column('diagnosis')
