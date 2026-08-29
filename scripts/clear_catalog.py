"""מחיקת תוכן הקטלוג. פעולה הרסנית.

מוחק את הקטלוג המשותף ואת השכבות הפרטיות שנתלות עליו:
מק"טים, יצרנים, קטגוריות, התאמות לרכב, מק"טים מקבילים, מחירים ומלאי.

*לא* מוחק ארגונים ומשתמשים - החשבונות נשארים, הם פשוט מתעוררים לקטלוג ריק.

הרצה מקומית:   python scripts/clear_catalog.py --yes
בפריסה:        RESET_CATALOG=1 (נצרך פעם אחת ב-preDeployCommand)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models import (  # noqa: E402
    Category,
    CrossReference,
    Fitment,
    Manufacturer,
    OrgPart,
    Part,
    PartSupplier,
    Supplier,
    db,
)

# סדר המחיקה חשוב: ילדים לפני הורים, אחרת מפתחות זרים חוסמים
DELETION_ORDER = [
    ("קישורי ספק-מק\"ט", PartSupplier),
    ("מחירים ומלאי", OrgPart),
    ("מק\"טים מקבילים", CrossReference),
    ("התאמות לרכב", Fitment),
    ("מק\"טים", Part),
    ("ספקים", Supplier),
    ("קטגוריות", Category),
    ("יצרנים", Manufacturer),
]


def clear_catalog(app):
    """מוחק את כל תוכן הקטלוג. מחזיר מילון של מה שנמחק."""
    with app.app_context():
        deleted = {}
        for label, model in DELETION_ORDER:
            count = model.query.count()
            if count:
                model.query.delete(synchronize_session=False)
            deleted[label] = count
        db.session.commit()

        for label, count in deleted.items():
            print(f"  נמחקו {count:>5} {label}")
        print(f'  סה"כ {sum(deleted.values())} רשומות.')
        return deleted


if __name__ == "__main__":
    import argparse

    from app import create_app

    parser = argparse.ArgumentParser(description="מחיקת תוכן הקטלוג")
    parser.add_argument(
        "--yes", action="store_true", help="אישור מפורש - חובה, הפעולה בלתי הפיכה"
    )
    args = parser.parse_args()
    if not args.yes:
        print("פעולה הרסנית. להרצה בפועל הוסף --yes")
        sys.exit(1)
    clear_catalog(create_app())
