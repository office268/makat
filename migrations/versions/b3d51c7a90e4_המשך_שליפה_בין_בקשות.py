"""המשך שליפה בין בקשות

מקור שמנווט בקטלוג (עמוד רכב -> קבוצה -> תרשים) עושה כמה הבאות
ברצף, וכל אחת היא עשרות שניות דרך ScraperAPI. שלושתן לא נכנסות
בתקציב של gunicorn, ולכן העבודה זוכרת לאן להמשיך והבקשה הבאה
מרימה משם.

Revision ID: b3d51c7a90e4
Revises: d7a4f0c82e19
"""
import sqlalchemy as sa
from alembic import op

revision = 'b3d51c7a90e4'
down_revision = 'd7a4f0c82e19'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('lookup_jobs') as batch:
        batch.add_column(sa.Column('resume_url', sa.String(length=500),
                                   nullable=True, server_default=''))
        batch.add_column(sa.Column('resume_hop', sa.Integer(),
                                   nullable=False, server_default='0'))


def downgrade():
    with op.batch_alter_table('lookup_jobs') as batch:
        batch.drop_column('resume_hop')
        batch.drop_column('resume_url')
