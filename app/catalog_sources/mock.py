"""מקור מדומה: אותו ממשק, בלי רשת ובלי מפתח.

קיים בשביל שני דברים. הראשון הוא בדיקות - כל הצנרת שמעליו (מטמון,
עבודה, אימות, כתיבה לקטלוג, מסך) נבדקת מקצה לקצה בלי לגעת באתר של
אף אחד. השני הוא פיתוח מקומי: ``CATALOG_SOURCES=mock`` נותן מסך
עובד למי שמפתח את התצוגה ואין לו מפתח API.

התשובה נגזרת מהשלדה ומסוג החלק, ולכן היא יציבה בין הרצות - אותה
בקשה מחזירה אותה תשובה, וזה מה שמאפשר לבדוק גם את המטמון.
"""
import hashlib

from ..taxonomy import type_name
from .base import Candidate, CatalogSource


def _brand(make):
    """המילה הראשונה של שם היצרן, כמו שהקטלוג שומר אותו."""
    words = (make or "").strip().split()
    return words[0] if words else "MOCK"


def _digest(*parts):
    raw = "|".join(str(part or "") for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest().upper()


class MockSource(CatalogSource):
    key = "mock"
    name = "מקור מדומה (בדיקות)"
    tier = "oem"
    needs_vin = False

    def lookup(self, vehicle, part_type, oem_numbers=(), fetcher=None, client=None):
        stamp = _digest(vehicle.get("vin"), vehicle.get("model"), part_type)
        oem = f"MOCK-{stamp[:8]}"
        return [
            Candidate(
                part_number=oem,
                manufacturer=_brand(vehicle.get("make")),
                tier="oem",
                oe_number=oem,
                oe_brand=_brand(vehicle.get("make")),
                image_url=f"https://example.invalid/{stamp[:8]}.jpg",
                source_url="https://example.invalid/mock",
                source_key=self.key,
                variant_key=stamp[:6],
                confidence="high",
                note=f"מקור מדומה · {type_name(part_type)}",
                extra={"name": type_name(part_type)},
            ),
            Candidate(
                part_number=f"MOCK-ALT-{stamp[8:14]}",
                manufacturer="MOCKTEC",
                tier="aftermarket",
                oe_number=oem,
                oe_brand=_brand(vehicle.get("make")),
                image_url=f"https://example.invalid/{stamp[8:14]}.jpg",
                price_listed=12.5,
                currency="EUR",
                source_url="https://example.invalid/mock",
                source_key=self.key,
                confidence="low",
                note="מקור מדומה · חלופי לא מאומת",
            ),
        ]
