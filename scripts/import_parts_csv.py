"""ייבוא מק"טים מקובץ CSV, מחוץ לממשק הווב.

  python scripts/import_parts_csv.py data/demo_parts.csv

בפריסה: IMPORT_PARTS_CSV=data/demo_parts.csv, ואז init_db טוען אותו.
בטוח להרצה חוזרת - מק"ט קיים מעודכן ולא משוכפל.

הקטלוג המשותף (מק"ט, יצרן, התאמות, מק"טים מקבילים) נטען לכל המערכת.
מחירים ומלאי הם שכבה פרטית לארגון, ולכן הם נטענים רק כשמוסרים ארגון.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import services  # noqa: E402


def load(app, csv_path, organization_id=None):
    path = Path(csv_path)
    if not path.exists():
        print(f"לא נמצא קובץ: {path}")
        return 0, 0, [f"לא נמצא קובץ: {path}"]

    with app.app_context(), path.open(encoding="utf-8-sig") as fh:
        created, updated, errors = services.import_csv(
            fh, organization_id=organization_id
        )
    print(f'  נוספו {created} מק"טים, עודכנו {updated}')
    for error in errors[:20]:
        print(f"  {error}")
    if len(errors) > 20:
        print(f"  ...ועוד {len(errors) - 20} שגיאות")
    return created, updated, errors


def main():
    import argparse

    from app import create_app

    parser = argparse.ArgumentParser(description="ייבוא מק\"טים מקובץ CSV")
    parser.add_argument("csv_path", help="נתיב לקובץ")
    parser.add_argument(
        "--org", type=int, help="מזהה ארגון - לטעינת מחירים ומלאי לשכבה הפרטית שלו"
    )
    args = parser.parse_args()

    _, _, errors = load(create_app(), args.csv_path, organization_id=args.org)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
