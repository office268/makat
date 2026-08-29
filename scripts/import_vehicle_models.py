"""ייבוא דגמי רכב ממאגר משרד התחבורה ב-data.gov.il.

המאגר פתוח (license: other-open), מתעדכן יומית ומכיל מעל 100 אלף רשומות -
שורה לכל שנת ייצור של כל דגם. הסקריפט מכווץ אותן לדגמים עם טווחי שנים.

  python scripts/import_vehicle_models.py              # הכל
  python scripts/import_vehicle_models.py --limit 5000 # דגימה
  python scripts/import_vehicle_models.py --file x.json  # מקובץ שכבר הורד

הרצה חוזרת בטוחה: דגם קיים מעודכן בטווח שנים רחב יותר, לא משוכפל.
"""
import argparse
import json
import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# שכבת הרשת משותפת עם הייבוא מתוך היישום - מימוש אחד, לא שניים
from app.vehicle_import import PAGE_SIZE, fetch_page  # noqa: E402,F401
from app.vehicle_catalog import collapse_records, upsert  # noqa: E402


def fetch_all(limit=None):
    """מושך עמוד אחרי עמוד. עוצר כשאין עוד רשומות או כשהגענו למגבלה."""
    records, offset, total = [], 0, None
    while True:
        page_size = PAGE_SIZE
        if limit is not None:
            page_size = min(PAGE_SIZE, limit - len(records))
            if page_size <= 0:
                break
        try:
            page, total = fetch_page(offset, page_size)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print(f"  שגיאת רשת ב-offset {offset}: {exc}")
            break
        if not page:
            break
        records.extend(page)
        offset += len(page)
        print(f"  נמשכו {len(records):,}" + (f" מתוך {total:,}" if total else ""))
        if total and offset >= total:
            break
    return records


def load(app, records):
    with app.app_context():
        rows = collapse_records(records)
        print(f"  {len(records):,} רשומות גולמיות → {len(rows):,} דגמים")
        created, updated = upsert(rows)
        print(f"  נוספו {created:,} דגמים, עודכנו {updated:,}")
        from app.vehicle_catalog import VehicleModel

        print(f'  סה"כ בקטלוג: {VehicleModel.query.count():,} דגמים')
        return created, updated


def main():
    parser = argparse.ArgumentParser(description="ייבוא דגמי רכב ממשרד התחבורה")
    parser.add_argument("--limit", type=int, help="מספר רשומות מרבי למשיכה")
    parser.add_argument("--file", help="קובץ JSON עם רשומות שכבר הורדו")
    args = parser.parse_args()

    from app import create_app

    app = create_app()

    if args.file:
        payload = json.loads(Path(args.file).read_text(encoding="utf-8"))
        records = payload if isinstance(payload, list) else payload.get("records", [])
        print(f"נטענו {len(records):,} רשומות מ-{args.file}")
    else:
        print("מושך מ-data.gov.il...")
        records = fetch_all(args.limit)
        if not records:
            print("לא התקבלו רשומות. אם הרשת חסומה, אפשר להוריד ידנית ולהשתמש ב---file.")
            return 1

    load(app, records)
    return 0


if __name__ == "__main__":
    sys.exit(main())
