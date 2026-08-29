"""הכנת בסיס הנתונים לפני עליית האפליקציה.

רץ כ-preDeployCommand ב-Railway: פעם אחת לכל דיפלוי, לפני שה-workers עולים.
בטוח להרצה חוזרת - יוצר רק טבלאות שחסרות, ולא מוחק שום דבר.

אם SEED_DEMO=1 והקטלוג ריק, נטען גם קטלוג הדמו. אם כבר יש מק"טים,
הזריעה מדלגת - כדי שדיפלוי לא ידרוס דאטה אמיתי.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask_migrate import upgrade  # noqa: E402

from app import create_app  # noqa: E402
from app.models import Part, db  # noqa: E402


def main():
    app = create_app()
    with app.app_context():
        print(f"בסיס נתונים: {db.engine.dialect.name}")
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
