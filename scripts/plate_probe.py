#!/usr/bin/env python
"""למה מספר רישוי אמיתי מחזיר "לא נמצא". תשובה, לא ניחוש.

מריץ מול מאגר משרד התחבורה את כל דרכי החיפוש, אחת-אחת, ומדפיס מה כל
אחת החזירה. זה מפריד בין שלוש אפשרויות שנראות זהות על המסך: המאגר לא
נגיש, המאגר ענה ואין רכב כזה, או שהשאילתה עצמה בנויה לא נכון.

    python scripts/plate_probe.py 107-32-802
    python scripts/plate_probe.py 10732802 --resource <id אחר>
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import vehicles  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plate", help="מספר רישוי, עם או בלי מקפים")
    parser.add_argument("--resource", action="append", default=[],
                        help="מזהה מאגר לבדיקה (ניתן לחזור). ברירת מחדל: המוגדרים")
    parser.add_argument("--json", action="store_true", help="פלט גולמי")
    parser.add_argument("--discover", action="store_true",
                        help="לסרוק את כל מאגרי הרכב ב-CKAN ולמצוא איפה הרכב יושב")
    args = parser.parse_args()

    digits = vehicles.normalize_plate(args.plate)
    print(f'מספר רישוי:  {args.plate}  ->  {digits} ({len(digits)} ספרות)')
    print(f"כתובת המאגר: {vehicles.CKAN_URL}")

    targets = [(r, r) for r in args.resource] or vehicles.resources()
    print(f"מאגרים:      {', '.join(label for _, label in targets)}\n")

    reached = False
    for resource_id, label in targets:
        print(f"── {label} ({resource_id})")
        for name, params in vehicles._strategies(digits):
            records, error = vehicles._query(resource_id, params)
            shown = json.dumps(params, ensure_ascii=False)[:70]
            if error:
                print(f"   ✗ {name:<16} {shown}\n       {error}")
                continue
            reached = True
            print(f"   {'✓' if records else '·'} {name:<16} {shown}"
                  f"  ->  {len(records)} שורות")
            if records:
                row = records[0]
                if args.json:
                    print(json.dumps(row, ensure_ascii=False, indent=2))
                else:
                    normalized = vehicles._normalize_record(row, "data.gov.il")
                    for key in ("make", "model", "year", "engine_code", "vin"):
                        print(f"       {key:<12} {normalized.get(key) or '—'}")
                print("\nנמצא. השאילתה הזו היא זו שעובדת.")
                return 0
        print()

    if not reached:
        print("אף שאילתה לא הגיעה למאגר - זו בעיית רשת או חסימה, לא מספר רישוי שגוי.")
        return 2

    print("המאגר ענה, ואין בו רכב עם המספר הזה.")
    if not args.discover:
        print("להרחבת החיפוש לכל מאגרי הרכב:  --discover")
        return 1

    print("\nסורק את כל מאגרי הרכב ב-CKAN...")
    found = vehicles.lookup_everywhere(args.plate)
    for entry in found.get("discovered") or []:
        mark = "·" if entry["known"] else " "
        print(f"   {mark} {entry['label']}  ({entry['resource']})")
    if found["status"] == "found":
        where = found.get("found_in") or {}
        print(f"\n✓ נמצא ב: {where.get('label')}  ({where.get('resource')})")
        for key in ("make", "model", "year", "engine_code", "vin"):
            print(f"     {key:<12} {found['vehicle'].get(key) or '—'}")
        print("\nלהוספה קבועה, כמשתנה סביבה:")
        print(f"GOV_VEHICLE_RESOURCES={vehicles.RESOURCE_ID}:רכב פרטי ומסחרי,"
              f"{where.get('resource')}:{where.get('label')}")
        return 0

    print("\nהרכב אינו באף מאגר שיש בו מספרי רישוי.")
    print("ייתכן שהמספר שגוי, או שהרכב אינו במרשם הפתוח.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
