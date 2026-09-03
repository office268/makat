"""ייבוא במנות: התקדמות אמיתית, ובקשה שלא נהרגת באמצע.

הייבוא שלח את כל הקובץ בבקשה אחת, ושם נפגשו שתי בעיות: 4,389 שורות
מול Postgres הן עשרות שניות, ו-gunicorn הורג בקשה אחרי ``WEB_TIMEOUT``.
קובץ גדול לא רק *נראה* תקוע - הוא נהרג באמצע, והמסך קיבל 502 בלי לומר
מה נכנס ומה לא.
"""
from app.models import Part

HEAD = "part_number,name_he,part_type"


def _rows(*rows):
    return HEAD + "\n" + "\n".join(rows)


def test_a_chunk_imports_and_reports_what_it_did(app, auth_client):
    response = auth_client.post("/import/chunk", data={
        "rows": _rows("17801-0T030,מסנן אוויר,air_filter",
                      "87139-0N010,מסנן מזגן,cabin_filter")})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["created"] == 2
    assert payload["error_count"] == 0
    with app.app_context():
        assert Part.query.filter_by(part_number="17801-0T030").first() is not None


def test_the_line_number_points_at_the_real_row_in_the_file(app, auth_client):
    """המנה השנייה מתחילה בשורה 302 של הקובץ, לא בשורה 2 שלה.

    בלי זה כל הודעת שגיאה הייתה מצביעה על תחילת הקובץ, ומי שמתקן היה
    מחפש במקום הלא נכון.
    """
    payload = auth_client.post("/import/chunk", data={
        "rows": _rows("58302-D3A00,רפידות,brake_pads_front"),
        "start_line": "302"}).get_json()
    assert payload["error_count"] == 1
    assert "שורה 302" in payload["errors"][0]


def test_a_bad_start_line_falls_back_instead_of_exploding(app, auth_client):
    for value in ("לא-מספר", "-5", ""):
        payload = auth_client.post("/import/chunk", data={
            "rows": _rows("58302-D3A00,רפידות,brake_pads_front"),
            "start_line": value}).get_json()
        assert "שורה 2" in payload["errors"][0]


def test_an_empty_chunk_is_a_clear_error_and_not_a_crash(app, auth_client):
    response = auth_client.post("/import/chunk", data={"rows": "   "})
    assert response.status_code == 400
    assert "שורות" in response.get_json()["error"]


def test_the_error_list_is_capped_but_the_count_is_not(app, auth_client):
    """קובץ שכולו פגום החזיר עשרות אלפי מחרוזות לדפדפן של מכונאי בטלפון."""
    bad = ["58302-D3A00,רפידות,brake_pads_front"] * 60
    payload = auth_client.post("/import/chunk", data={"rows": _rows(*bad)}).get_json()
    assert payload["error_count"] == 60
    assert len(payload["errors"]) == 50


def test_a_file_split_into_chunks_lands_whole(app, auth_client):
    """שתי מנות, ושתיהן נכנסות - זו כל ההבטחה של החלוקה."""
    first = auth_client.post("/import/chunk", data={
        "rows": _rows("17801-0T030,מסנן אוויר,air_filter")}).get_json()
    second = auth_client.post("/import/chunk", data={
        "rows": _rows("90915-YZZD4,מסנן שמן,oil_filter"), "start_line": "3"}).get_json()
    assert first["created"] == second["created"] == 1
    with app.app_context():
        assert Part.query.count() >= 2


def test_the_finish_call_writes_one_activity_row(app, auth_client):
    from app.activity import ActivityLog

    auth_client.post("/import/finish", data={
        "filename": "makat.csv", "created": "4348", "updated": "0", "errors": "41"})
    with app.app_context():
        note = ActivityLog.query.order_by(ActivityLog.id.desc()).first()
        assert "makat.csv" in note.summary
        assert "4348" in note.summary
        assert "41" in note.summary


def test_finish_survives_junk_counters(app, auth_client):
    response = auth_client.post("/import/finish", data={
        "filename": "x.csv", "created": "הרבה", "updated": "-3", "errors": ""})
    assert response.status_code == 200


def test_a_visitor_cannot_import_a_chunk(app, client):
    client.post("/logout")
    response = client.post("/import/chunk", data={"rows": _rows("1,א,oil_filter")})
    assert response.status_code in (302, 401, 403)
