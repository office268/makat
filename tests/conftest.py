import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app  # noqa: E402
from app.config import TestConfig  # noqa: E402
from app.models import CrossReference, Fitment, Part, db  # noqa: E402
from app.services import get_or_create_category, get_or_create_manufacturer  # noqa: E402


@pytest.fixture
def app():
    application = create_app(TestConfig)
    with application.app_context():
        db.drop_all()
        db.create_all()
        part = Part(
            part_number="TEST-001",
            name_he="רפידות בלם קדמיות COROLLA",
            part_type="brake_pads_front",
            manufacturer=get_or_create_manufacturer("TRW"),
            category=get_or_create_category("בלמים"),
            price=200.0,
            cost=140.0,
            stock_qty=4,
            min_stock=2,
        )
        part.cross_refs = [
            CrossReference(ref_number="04465-02220", ref_type="OEM", ref_brand="Toyota")
        ]
        part.fitments = [
            Fitment(make="טויוטה", model="COROLLA", year_from=2013, year_to=2018,
                    engine_code="1ZR-FE")
        ]
        db.session.add(part)
        db.session.commit()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()
