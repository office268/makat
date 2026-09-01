"""כלל התאמת שם הדגם, ושני המימושים שלו.

הכלל כתוב פעמיים: ``model_matches_name`` בפייתון, ו-``_model_matches``
כביטוי SQL. שניהם מזינים את אותם מסכים - החיפוש, ספירת המק"טים לרכב,
עמודות הקטלוג ומספרי הצי - ולכן אי-הסכמה ביניהם אינה באג בפינה אלא
טבלה שממוינת לפי מספר אחד ומציגה מספר אחר.

הקובץ הזה הוא רשת הביטחון על ההסכמה הזאת. היא לא הייתה קיימת, וזו
הסיבה שהשניים נפרדו מלכתחילה.
"""
import csv
from pathlib import Path

import pytest

from app import services
from app.models import Fitment, Part, db

CATALOG = Path(__file__).resolve().parent.parent / "data" / "parts_catalog.csv"


# ---------------------------------------------------------------------------
# הכלל עצמו
# ---------------------------------------------------------------------------

MATCH = [
    ("RAV 4", "RAV4", "רווח אינו מידע"),
    ("RAV4", "RAV 4", "וגם בכיוון ההפוך"),
    ("COROLLA", "COROLLA VERSO", "שם הקטלוג ארוך יותר"),
    ("COROLLA HSD SDN", "COROLLA", "שם המרשם ארוך יותר - הבאג מהייצור"),
    ("COROLLA CROSS", "COROLLA", "אותו דבר, גימור אחר"),
    ("RAV4 HYBRID", "RAV4", "גימור אחרי שם מלא"),
    ("CX-5 AWD", "CX-5", "גימור אחרי שם עם מקף"),
    ("MAZDA 3", "3", 'הקטלוג רשם רק "3", והיצרן כבר סונן'),
    ("3", "MAZDA 3", "ואותו דבר מהכיוון השני"),
    ("C-HR", "C-HR", "מקף בתוך שם אחד"),
    ("I20", "i20", "רישיות"),
]

NO_MATCH = [
    ("CX-3", "CX-30", "ספרה שנמשכת היא דגם אחר, לא גימור"),
    ("CX-30", "CX-3", "ובאותה מידה מהכיוון ההפוך"),
    ("i10", "i100", "אותו דפוס ביונדאי"),
    ("MAZDA 3", "CX-3", "3 אינו מילה בתוך CX-3"),
    ("MAZDA 6", "3", "מילה שאינה שם"),
    ("CEED", "PROCEED", "הכלה שאינה מילה"),
    ("SOUL", "SOULEV", "ולא קידומת שנמשכת לאותה מילה"),
    ("I35", "I3", "שם קצר מדי מכדי להיות קידומת"),
    ("COROLLA", "", "בלי שם אין התאמה"),
    ("", "COROLLA", "וגם לא בכיוון הזה"),
]


@pytest.mark.parametrize("registry,catalog,why", MATCH)
def test_names_that_mean_the_same_car(registry, catalog, why):
    assert services.model_matches_name(registry, catalog) is True, why


@pytest.mark.parametrize("registry,catalog,why", NO_MATCH)
def test_names_that_mean_different_cars(registry, catalog, why):
    assert services.model_matches_name(registry, catalog) is False, why


# ---------------------------------------------------------------------------
# שני המימושים מסכימים
# ---------------------------------------------------------------------------

ALL_NAMES = sorted({name for pair in MATCH + NO_MATCH for name in pair[:2] if name} | {
    "CX-3", "CX-30", "CX-5", "MX-5", "MAZDA 2", "MAZDA 3", "MAZDA 6",
    "COROLLA", "COROLLA VERSO", "RAV 4", "RAV4", "C-HR", "i10", "i20", "i30",
    "RIO", "NIRO", "CEED", "SOUL", "PICANTO", "3", "6",
})


def test_sql_and_python_agree_on_every_pair(app):
    """אותם שמות, שני המימושים, ותשובה זהה לכל צמד.

    זו הבדיקה שהייתה חסרה. בלעדיה אפשר לתקן כלל בצד אחד ולהשאיר את
    השני מאחור, והמסך יראה מספר אחד ויסדר לפי אחר.
    """
    with app.app_context():
        for index, name in enumerate(ALL_NAMES):
            part = Part(part_number=f"MM-{index}", name_he="בדיקה")
            part.fitments.append(Fitment(make="בדיקה", model=name))
            db.session.add(part)
        db.session.commit()

        disagreements = []
        for registry in ALL_NAMES:
            in_sql = {
                row[0] for row in db.session.query(Fitment.model)
                .filter(Fitment.make == "בדיקה")
                .filter(services._model_matches(registry))
                .all()
            }
            in_python = {
                name for name in ALL_NAMES
                if services.model_matches_name(registry, name)
            }
            if in_sql != in_python:
                disagreements.append(
                    f"{registry!r}: SQL={sorted(in_sql)} פייתון={sorted(in_python)}"
                )
        assert not disagreements, "\n".join(disagreements)


# ---------------------------------------------------------------------------
# מול הקטלוג האמיתי
# ---------------------------------------------------------------------------

def _catalog_models():
    """{יצרן: שמות הדגמים}, מקובץ הקטלוג שנשלח לייצור."""
    by_make = {}
    with open(CATALOG, encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            for chunk in (row.get("fitments") or "").split(";"):
                fields = [value.strip() for value in chunk.split(":")]
                if len(fields) >= 2 and fields[0] and fields[1]:
                    by_make.setdefault(fields[0], set()).add(fields[1])
    return by_make


@pytest.mark.skipif(not CATALOG.exists(), reason="קובץ הקטלוג אינו בריפו")
def test_no_model_pulls_another_models_parts(app):
    """הבדיקה שסופרת: על הקטלוג האמיתי, אף רכב אינו מושך חלפים של דגם אחר.

    קודם CX-3 משך את החלפים של CX-30 ולהפך - 111 התאמות לדגם הלא נכון.
    """
    collisions = [
        f"{make}: {first!r} מושך את החלפים של {second!r}"
        for make, models in _catalog_models().items()
        for first in sorted(models)
        for second in sorted(models)
        if first != second and services.model_matches_name(first, second)
    ]
    assert not collisions, "\n".join(collisions)
