"""מקורות קטלוג חיצוניים - מאיפה מגיע מק"ט שאין לנו בקטלוג.

הזרימה שמאחורי המודול הזה: מספר רישוי -> מספר שלדה -> מק"ט מקורי ->
מק"טים חלופיים. שני שלבים, שני עולמות:

  * ``tier="oem"``          - קטלוג היצרן, מחפש לפי שלדה ומחזיר את המק"ט
                              המקורי ואת תרשים הפיצוץ. זו התשובה המדויקת
                              לרכב הזה, ולא לדגם הזה.
  * ``tier="aftermarket"``  - קטלוג חלופים, מחפש לפי המק"ט המקורי ומחזיר
                              את מה שמוסך באמת מוכר, עם תמונת מוצר ומחיר.

מספר ה-OE הוא הציר בין השניים, והוא כבר קיים בסכימה
(``CrossReference(ref_type="OEM")``) - מה שחסר היה רק מי ימלא אותו
מתוך שלדה.

כל מקור עומד מאחורי אותו ממשק, כדי שהחלפת אתר - ובבוא היום מעבר
למקור מורשה בתשלום - תהיה קובץ אחד ולא ניתוח לב.
"""
import os

from .aftermarket import AftermarketSource
from .base import Candidate, CatalogSource  # noqa: F401 - חלק מהממשק הציבורי
from .epc_vin import EpcVinSource
from .laximo import LaximoSource
from .mock import MockSource
from .tecdoc import TecDocSource

# המקורות שקיימים בקוד. ``CATALOG_SOURCES`` בוחר מי מהם רץ בפועל.
REGISTRY = {
    source.key: source
    for source in (
        LaximoSource(),        # שלדה -> מק"ט מקורי, מקטלוג היצרן
        TecDocSource(),        # מק"ט מקורי -> חלופים, עם תמונת מוצר
        EpcVinSource(),        # אותו שלב כמו Laximo, מול אתר קטלוגי כללי
        AftermarketSource(),   # אותו שלב כמו TecDoc, מול אתר קטלוגי כללי
        MockSource(),          # בלי רשת ובלי מפתח
    )
}

# Laximo ואחריו TecDoc: הראשון מוציא מהשלדה את המספר המקורי, והשני
# מחפש לפיו. epc/aftermarket נשארים כחלופה גנרית לאתר אחר.
DEFAULT_ORDER = "laximo,tecdoc"


def enabled_keys():
    """שמות המקורות שירוצו, לפי הסדר שבו הם ירוצו.

    הסדר הוא המהות: ``epc`` מייצר את מספר ה-OE ש-``aftermarket`` מחפש
    לפיו. היפוך הסדר משאיר את השלב השני בלי מפתח.
    """
    raw = os.environ.get("CATALOG_SOURCES", DEFAULT_ORDER)
    return [key.strip() for key in raw.split(",") if key.strip() in REGISTRY]


def enabled_sources():
    return [REGISTRY[key] for key in enabled_keys()]


def get(key):
    return REGISTRY.get(key)
