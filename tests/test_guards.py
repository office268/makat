"""נעילת הכתיבה - עד שמנגנון ההרשאות ייכנס."""
import pytest

from app import create_app
from app.config import TestConfig
from app.models import Part, db


class ReadOnlyConfig(TestConfig):
    READ_ONLY = True


@pytest.fixture
def ro_client():
    app = create_app(ReadOnlyConfig)
    with app.app_context():
        db.drop_all()
        db.create_all()
        db.session.add(Part(part_number="RO-1", name_he="חלק"))
        db.session.commit()
        part_id = Part.query.first().id
        yield app.test_client(), part_id
        db.session.remove()
        db.drop_all()


def test_api_writes_are_blocked(ro_client):
    client, part_id = ro_client
    assert client.post("/api/parts", json={"part_number": "X", "name_he": "y"}).status_code == 403
    assert client.put(f"/api/parts/{part_id}", json={"price": 1}).status_code == 403
    assert client.delete(f"/api/parts/{part_id}").status_code == 403


def test_web_writes_are_blocked(ro_client):
    client, part_id = ro_client
    for path in [f"/parts/{part_id}/delete", "/parts/new", "/import"]:
        assert client.post(path).status_code == 302, path
    # ולא נמחק דבר
    assert client.get(f"/parts/{part_id}").status_code == 200


def test_reads_still_work(ro_client):
    client, part_id = ro_client
    for path in ["/", "/parts", "/demo", "/api/parts", "/api/stats", "/export.csv",
                 f"/parts/{part_id}", "/parts/new"]:
        assert client.get(path).status_code == 200, path


def test_demo_search_still_works(ro_client):
    """POST /demo הוא חיפוש, לא שינוי - חייב להמשיך לעבוד."""
    client, _ = ro_client
    response = client.post("/demo", data={"plate": "12345678", "query": "רפידות קדמיות"})
    assert response.status_code == 200
    assert "COROLLA" in response.get_data(as_text=True)


def test_writes_work_when_guard_is_off(client):
    """בפיתוח מקומי הנעילה כבויה והכל פתוח."""
    assert client.post(
        "/api/parts", json={"part_number": "OPEN-1", "name_he": "חלק"}
    ).status_code == 201
