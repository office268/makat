"""הכנת בסיס הנתונים לפני עליית האפליקציה.

משתני סביבה:
  RESET_CATALOG=1   מוחק את תוכן הקטלוג (הרסני; ארגונים ומשתמשים נשארים)
  SEED_DEMO=1       טוען קטלוג דמו אם הקטלוג ריק
  SEED_DEMO=force   בונה מחדש את קטלוג הדמו גם אם יש בו נתונים


רץ כ-preDeployCommand ב-Railway: פעם אחת לכל דיפלוי, לפני שה-workers עולים.
בטוח להרצה חוזרת - יוצר רק טבלאות שחסרות, ולא מוחק שום דבר.

אם SEED_DEMO=1 והקטלוג ריק, נטען גם קטלוג הדמו. אם כבר יש מק"טים,
הזריעה מדלגת - כדי שדיפלוי לא ידרוס דאטה אמיתי.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask_migrate import stamp, upgrade  # noqa: E402
from sqlalchemy import inspect  # noqa: E402

from app import create_app  # noqa: E402
from app.models import Part, db  # noqa: E402

# המיגרציה שמתארת את הסכימה כפי שהייתה כשעוד נוצרה ב-create_all()
BASELINE_REVISION = "e8b168d830d4"


def _adopt_pre_alembic_database():
    """מאמץ בסיס נתונים שנוצר ב-create_all() לפני שהיה Alembic.

    בסיס כזה מכיל טבלאות אבל לא alembic_version, ולכן Alembic מנסה
    להריץ את המיגרציה הראשונה ונופל על "relation already exists".

    הפתרון: create_all() משלים רק את הטבלאות החסרות (הוא checkfirst),
    ואז מסמנים את קו הבסיס. מכאן והלאה upgrade() מריץ רק את המיגרציות
    שבאמת חסרות. מחזיר True אם אומץ בסיס קיים.
    """
    tables = set(inspect(db.engine).get_table_names())
    if "alembic_version" in tables or not tables:
        return False

    print("  זוהה בסיס נתונים שקדם ל-Alembic. מאמץ אותו...")

    # יוצרים רק את הטבלאות שקו הבסיס מגדיר וחסרות בפועל. create_all()
    # ללא הגבלה היה יוצר גם טבלאות ממיגרציות מאוחרות יותר (org_parts,
    # invitations), והן היו מתנגשות כשה-upgrade יגיע אליהן.
    from app.auth_models import Organization, User

    baseline_tables = [Organization.__table__, User.__table__]
    missing = [t for t in baseline_tables if t.name not in tables]
    if missing:
        print(f"  משלים טבלאות חסרות: {', '.join(t.name for t in missing)}")
        db.metadata.create_all(bind=db.engine, tables=missing)

    stamp(revision=BASELINE_REVISION)
    print(f"  סומן בקו הבסיס {BASELINE_REVISION}.")
    return True


def main():
    app = create_app()
    with app.app_context():
        print(f"בסיס נתונים: {db.engine.dialect.name}")
        _adopt_pre_alembic_database()
        upgrade()
        print("המיגרציות הורצו.")

        count = Part.query.count()
        print(f'בקטלוג {count} מק"טים.')

        if os.environ.get("SEED_DEMO") == "1" and count == 0:
            from scripts.seed import seed

            print("הקטלוג ריק ו-SEED_DEMO=1 - טוען קטלוג דמו...")
            seed(app, reset=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
