"""מסלול סריקה בספירת הצי

Revision ID: c5b8f31d97ae
Revises: a7d0e4c81b52
Create Date: 2026-08-30 21:35:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c5b8f31d97ae'
down_revision = 'a7d0e4c81b52'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('fleet_stats_jobs', schema=None) as batch_op:
        # באיזה מסלול ההרצה נמצאת: ספירה אצל המאגר או סריקה מלאה
        batch_op.add_column(
            sa.Column('mode', sa.String(length=10), nullable=False, server_default='sql')
        )
        # סה"כ הרשומות במאגר - קיים בסריקה בלבד, ומאפשר אחוז התקדמות אמיתי
        batch_op.add_column(sa.Column('total', sa.Integer(), nullable=True))
        # מצב הספירה הצבור בין מנה למנה, דחוס
        batch_op.add_column(sa.Column('counts', sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table('fleet_stats_jobs', schema=None) as batch_op:
        batch_op.drop_column('counts')
        batch_op.drop_column('total')
        batch_op.drop_column('mode')
