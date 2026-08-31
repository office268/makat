"""מקור ההרצה בעבודות הגילוי

Revision ID: c3a91d4e7b62
Revises: b7e3f2a94c15
Create Date: 2026-08-31 22:40:00.000000

הגריד של Autodoc נכנס לקטלוג באותה צנרת של הגילוי דרך המודל - אותן
מטרות, אותו אימות, אותה שמירה - ולכן הוא חולק את טבלת ההרצות. העמודה
הזאת היא מה שמבדיל ביניהן, וכל מה שכבר קיים הוא חיפוש של Claude.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c3a91d4e7b62'
down_revision = 'b7e3f2a94c15'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('discovery_jobs', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('source', sa.String(length=20), nullable=False,
                      server_default='claude')
        )
        batch_op.create_index(
            batch_op.f('ix_discovery_jobs_source'), ['source'], unique=False
        )


def downgrade():
    with op.batch_alter_table('discovery_jobs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_discovery_jobs_source'))
        batch_op.drop_column('source')
