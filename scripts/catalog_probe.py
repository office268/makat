#!/usr/bin/env python
"""בדיקה חיה של מקור קטלוגי, מהשורה - בלי להריץ את האפליקציה.

זה הכלי שהופך "צריך לכוון את התבנית מול האתר" לפעולה של דקה: נותנים לו
מספר רישוי או שלדה, הוא רץ את המקור בדיוק כמו שהאפליקציה תריץ אותו,
ומדפיס מה קרה בכל שלב - איזו כתובת נפתחה, כמה תווים חזרו, ומה המודל
הוציא מהם.

    python scripts/catalog_probe.py --plate 12345678 --part oil_filter
    python scripts/catalog_probe.py --vin JTDBR32E560095678 --source tecdoc \
        --oem 04152-YZZA1
    python scripts/catalog_probe.py --plate 12345678 --save tests/fixtures/laximo.html

``--save`` שומר את התשובה הגולמית כ-fixture. זה מה שהופך בדיקה ידנית
מוצלחת לבדיקה אוטומטית שתתפוס את השינוי הבא באתר.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import catalog_sources  # noqa: E402
from app import vehicles  # noqa: E402
from app.catalog_sources import base, trace  # noqa: E402
from app.taxonomy import PART_TYPES, type_name  # noqa: E402


def resolve_vehicle(args):
    """הרכב שעליו בודקים: מהמרשם לפי רישוי, או שלדה שנמסרה ביד."""
    if args.plate:
        vehicle = vehicles.lookup(args.plate)
        if not vehicle:
            sys.exit(f'לא נמצא רכב עבור מספר רישוי "{args.plate}".')
        if args.vin:
            vehicle["vin"] = args.vin
        return vehicle
    return {
        "plate": "", "vin": args.vin or "", "make": args.make, "model": args.model,
        "year": args.year, "engine_code": args.engine, "model_code": "",
        "source": "manual",
    }


def describe(vehicle):
    line = " · ".join(
        str(value) for value in (
            vehicle.get("make"), vehicle.get("model"), vehicle.get("year"),
            vehicle.get("engine_code"),
        ) if value
    )
    print(f"רכב:   {line or '—'}")
    print(f"שלדה:  {vehicle.get('vin') or '— (אין במרשם)'}")
    print(f"מקור:  {vehicle.get('source')}")


class Recorder:
    """עוטף את ההבאה כדי לדווח מה נמשך ולשמור אותו."""

    def __init__(self, inner, save_to=None):
        self.inner = inner
        self.save_to = save_to
        self.pages = []

    def __call__(self, url, timeout=None):
        print(f"  → מביא: {url}")
        body = self.inner(url, timeout=timeout)
        print(f"    התקבלו {len(body):,} תווים")
        self.pages.append((url, body))
        if self.save_to:
            Path(self.save_to).write_text(body, encoding="utf-8")
            print(f"    נשמר ל-{self.save_to}")
        return body


def _uses_api(source):
    return bool(getattr(source, "use_api", lambda: False)())


def _record_api(module, save_to):
    """עוטף את קריאת ה-API של המקור, כדי לדווח ולשמור את התשובה."""
    original = getattr(module, "call_api", None)
    if original is None:
        return

    def wrapped(*args, **kwargs):
        print(f"  → קורא ל-API: {getattr(module, 'API_URL', '')}")
        body = original(*args, **kwargs)
        print(f"    התקבלו {len(body):,} תווים")
        if save_to:
            Path(save_to).write_text(body, encoding="utf-8")
            print(f"    נשמר ל-{save_to}")
        return body

    module.call_api = wrapped


def _print_trace():
    """יומן החקירה, בדיוק אותו יומן שהמסך מציג. מודפס גם בכשל -
    שם הוא הכי שווה."""
    lines = trace.lines()
    if not lines:
        return
    print("\nיומן:")
    for line in lines:
        print(f"  {line}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plate", help="מספר רישוי - הרכב יילקח מהמרשם")
    parser.add_argument("--vin", help="מספר שלדה (דורס את זה שבמרשם)")
    parser.add_argument("--make", default="", help="יצרן, כשאין רישוי")
    parser.add_argument("--model", default="", help="דגם, כשאין רישוי")
    parser.add_argument("--year", type=int, help="שנה, כשאין רישוי")
    parser.add_argument("--engine", default="", help="קוד מנוע, כשאין רישוי")
    parser.add_argument("--source", default="laximo",
                        help=f"מקור: {', '.join(sorted(catalog_sources.REGISTRY))}")
    parser.add_argument("--part", default="oil_filter", help="מפתח סוג חלק")
    parser.add_argument("--oem", action="append", default=[],
                        help="מספר מקורי לשלב החלופים (ניתן לחזור)")
    parser.add_argument("--save", help="שמירת התשובה הגולמית לקובץ fixture")
    parser.add_argument("--browser", action="store_true",
                        help="לכפות מסלול web (דפדפן/ScraperAPI) גם כשמוגדר API")
    parser.add_argument("--fetcher", choices=["auto", "scraperapi", "browser", "direct"],
                        help="במי להשתמש להבאת הדף")
    parser.add_argument("--account", action="store_true",
                        help="מציג את מצב חשבון ScraperAPI ויוצא")
    args = parser.parse_args()

    if args.account:
        from app.catalog_sources import scraperapi

        if not scraperapi.configured():
            print("אין SCRAPERAPI_KEY בסביבה.")
            return 1
        print(scraperapi.account())
        return 0

    if args.fetcher:
        base.FETCHER = args.fetcher

    if not (args.plate or args.vin or args.make):
        parser.error("צריך --plate, או --vin, או --make/--model")
    if args.part not in PART_TYPES:
        parser.error(f"סוג חלק לא מוכר: {args.part}")
    source = catalog_sources.get(args.source)
    if source is None:
        parser.error(f"אין מקור בשם {args.source}")

    vehicle = resolve_vehicle(args)
    describe(vehicle)
    print(f"חלק:   {type_name(args.part)}")
    print(f"מקור:  {source.name} (tier={source.tier})")
    print(f"הבאה:  {base.fetcher_kind()}")

    if not base.parser_available():
        print("\n⚠ אין ANTHROPIC_API_KEY - אפשר להביא את הדף, אבל לא לפענח אותו.")

    # ה-MODE נקרא בזמן ייבוא, ולכן כפייה למסלול דפדפן היא שינוי של
    # התכונה במודול - הגדרת משתנה סביבה כאן כבר מאוחרת מדי.
    module = sys.modules.get(type(source).__module__)
    if args.browser and hasattr(module, "MODE"):
        module.MODE = "web"

    fetcher = None
    if args.browser or (args.save and not _uses_api(source)):
        fetcher = Recorder(base.default_fetcher(), save_to=args.save)
    elif args.save:
        _record_api(module, args.save)

    print("\nמריץ...")
    trace.start()
    try:
        found = source.lookup(
            vehicle, args.part, oem_numbers=args.oem, fetcher=fetcher
        )
    except Exception as exc:
        _print_trace()
        print(f"\n✗ נכשל: {type(exc).__name__}: {exc}")
        return 1
    _print_trace()

    if not found:
        print("\nלא הוחזר אף מק\"ט.")
        return 0
    print(f"\nנמצאו {len(found)} מועמדים:\n")
    for candidate in found:
        print(f"  {candidate.part_number}  [{candidate.tier}/{candidate.confidence}]")
        print(f"    יצרן:  {candidate.manufacturer or '—'}")
        print(f"    OE:    {candidate.oe_number or '—'}")
        print(f"    תמונה: {candidate.image_url or '—'}")
        print(f"    מקור:  {candidate.source_url or '—'}")
        if candidate.note:
            print(f"    הערה:  {candidate.note}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
