"""תרשים פיצוץ על המק"ט

Revision ID: d7a4f0c82e19
Revises: c9d2e5a71b40
Create Date: 2026-09-01 21:30:00.000000

תרשים הפיצוץ מגיע מקטלוג היצרן (Laximo) יחד עם המק"ט המקורי. עד היום
הוא נדחס לתוך image_url - אותו שדה של תצלום המוצר - ולכן הוצג כתמונה
ממוזערת בגודל 64 פיקסלים, שזה בדיוק הגודל שבו סכמה עם מספרי חלקים
הופכת לכתם. שדה נפרד, וגם מקום לשמור אותו: בלעדיו החיפוש הבא לאותו
דגם נענה מהקטלוג המקומי ומאבד את התרשים.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd7a4f0c82e19'
down_revision = 'c9d2e5a71b40'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("parts", schema=None) as batch_op:
        batch_op.add_column(sa.Column("diagram_url", sa.String(length=500), nullable=True))


def downgrade():
    with op.batch_alter_table("parts", schema=None) as batch_op:
        batch_op.drop_column("diagram_url")
