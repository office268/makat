"""כמה רכבים מכל דגם פעילים בישראל - ספירה ממרשם משרד התחבורה.

  python scripts/vehicle_stats.py                      # ספירה -> data/fleet_by_model.csv
  python scripts/vehicle_stats.py --load               # + טעינה למסד, בשביל /stats
  python scripts/vehicle_stats.py --min-count 100      # רק דגמים עם 100 רכבים ומעלה
  python scripts/vehicle_stats.py --scan --load        # כשנקודת ה-SQL של המאגר סגורה
  python scripts/vehicle_stats.py --file rechev.csv    # מקובץ המאגר שהורד ידנית

ברירת המחדל מבקשת מהמאגר לספור בעצמו (GROUP BY אחד) ומחזירה עשרות אלפי
שורות. --scan מושך את שלושת מיליון הרכבים בעמודים של אלף וסופר מקומית:
אותה תוצאה, הרבה יותר לאט - מסלול חירום, לא ברירת מחדל.
"""
import argparse
import csv
import json
import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.fleet_stats import (  # noqa: E402
    aggregate_records,
    fetch_counts,
    replace_snapshot,
    scan_counts,
    sort_rows,
    to_csv,
)

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "data" / "fleet_by_model.csv"
NETWORK_ERRORS = (urllib.error.URLError, TimeoutError, OSError, ValueError)


def from_file(path):
    """צובר מקובץ המאגר שהורד ידנית - CSV או JSON.

    CSV נקרא בזרימה: הקובץ המלא של המאגר הוא שלושה מיליון שורות, וטעינה
    שלו כולו לזיכרון רק כדי לספור היא בזבוז מיותר.
    """
    path = Path(path)
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        records = payload if isinstance(payload, list) else payload.get("records", [])
        return aggregate_records(records)

    counts = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        aggregate_records(csv.DictReader(handle), counts)
    return counts


def report(rows, top=15):
    total = sum(row["vehicles"] for row in rows)
    print(f'\nסה"כ {total:,} רכבים פעילים ב-{len(rows):,} דגמים')
    if not rows:
        return
    print(f"\n{top} הדגמים הנפוצים:")
    for index, row in enumerate(rows[:top], 1):
        share = row["vehicles"] * 100.0 / total if total else 0
        print(f'  {index:>2}. {row["make"]} {row["model"]:<20} '
              f'{row["vehicles"]:>9,}  {share:5.2f}%')


def main():
    parser = argparse.ArgumentParser(description="פילוח הרכבים הפעילים בישראל לפי דגם")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="קובץ CSV לכתיבה")
    parser.add_argument("--load", action="store_true", help="טעינה למסד עבור /stats")
    parser.add_argument("--scan", action="store_true", help="דפדוף במקום שאילתת SQL")
    parser.add_argument("--file", help="קובץ CSV/JSON של המאגר שהורד ידנית")
    parser.add_argument("--min-count", type=int, default=1,
                        help="סף מזערי של רכבים לדגם")
    parser.add_argument("--limit", type=int, help="מגבלת שורות/רשומות, לבדיקה מהירה")
    args = parser.parse_args()

    try:
        if args.file:
            print(f"סופר מתוך {args.file}...")
            rows = sort_rows(from_file(args.file).values(), args.min_count)
        elif args.scan:
            print("מדפדף במאגר וסופר מקומית - זה לוקח זמן...")
            rows = scan_counts(
                max_records=args.limit,
                min_count=args.min_count,
                progress=lambda seen, total: print(
                    f"  נסרקו {seen:,}" + (f" מתוך {total:,}" if total else ""), end="\r"
                ),
            )
            print()
        else:
            print("מבקש מהמאגר את הפילוח (GROUP BY)...")
            rows = fetch_counts(
                min_count=args.min_count,
                max_rows=args.limit,
                progress=lambda count: print(f"  {count:,} דגמים", end="\r"),
            )
            print()
    except NETWORK_ERRORS as exc:
        print(f"\nהמאגר לא נענה: {exc}")
        print("אם נקודת ה-SQL חסומה נסה --scan, ואם הרשת חסומה לגמרי --file "
              "עם הקובץ שהורד מ-data.gov.il.")
        return 1

    if not rows:
        print("לא התקבלו נתונים.")
        return 1

    Path(args.out).write_text(to_csv(rows), encoding="utf-8")
    print(f"נכתב {args.out}")
    report(rows)

    if args.load:
        from app import create_app

        app = create_app()
        with app.app_context():
            saved = replace_snapshot(rows)
        print(f"\nנטענו {saved:,} דגמים למסד - הפילוח זמין ב-/stats")
    return 0


if __name__ == "__main__":
    sys.exit(main())
