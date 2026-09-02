"""פקיעת זמן אינה "לא נגיש", ותקציב הזמן חייב להיות גלוי.

הריצה שחשפה את זה: PartSouq מחזיר את הרכב הנכון (נבדק מול האתר החי),
ובכל זאת השליפה נכשלה ב-``ScraperAPI לא נגיש: The read operation timed
out``. ההודעה שולחת לבדוק רשת, בזמן שמה שצריך הוא לשנות מספר בהגדרות.
"""
import urllib.error

import pytest

from app.catalog_sources import scraperapi, trace


@pytest.mark.parametrize("exc,expected", [
    (TimeoutError("timed out"), True),
    # urllib עוטף timeout של socket ב-URLError, וזה המקרה שקרה בשטח
    (urllib.error.URLError(TimeoutError("timed out")), True),
    (urllib.error.URLError("The read operation timed out"), True),
    (urllib.error.URLError("Name or service not known"), False),
    (OSError("connection refused"), False),
])
def test_a_timeout_is_told_apart_from_an_unreachable_service(exc, expected):
    assert scraperapi._timed_out(exc) is expected


def test_the_timeout_message_says_which_knob_to_turn():
    text = scraperapi._explain_timeout(40, render=True)
    assert "40 שניות" in text
    assert "SCRAPERAPI_RENDER=0" in text
    assert "SCRAPERAPI_TIMEOUT" in text
    assert "WEB_TIMEOUT" in text
    # ‏"לא נגיש" הוא בדיוק מה שההודעה הזו באה להחליף
    assert "לא נגיש" not in text


def test_without_render_the_message_does_not_suggest_turning_it_off():
    text = scraperapi._explain_timeout(40, render=False)
    assert "SCRAPERAPI_RENDER=0" not in text
    assert "SCRAPERAPI_TIMEOUT" in text


def test_a_budget_that_leaves_room_is_not_warned_about(monkeypatch):
    monkeypatch.setattr(scraperapi, "TIMEOUT", 40.0)
    monkeypatch.setattr(scraperapi, "WEB_TIMEOUT", 90.0)
    monkeypatch.setattr(scraperapi, "MODEL_BUDGET", 15.0)
    assert scraperapi.budget_warning() == ""


def test_a_budget_with_no_room_under_gunicorn_is_warned_about(monkeypatch):
    """המקרה המסוכן: gunicorn הורג את העובד באמצע, והמשתמש לא מקבל
    אפילו הודעת שגיאה."""
    monkeypatch.setattr(scraperapi, "TIMEOUT", 90.0)
    monkeypatch.setattr(scraperapi, "WEB_TIMEOUT", 60.0)
    monkeypatch.setattr(scraperapi, "MODEL_BUDGET", 15.0)
    warning = scraperapi.budget_warning()
    assert "SCRAPERAPI_TIMEOUT=90" in warning
    assert "WEB_TIMEOUT=60" in warning


def test_the_default_settings_leave_room(monkeypatch):
    """ברירות המחדל שבקוד חייבות להיות עקביות זו עם זו."""
    monkeypatch.setattr(scraperapi, "TIMEOUT", 40.0)
    monkeypatch.setattr(scraperapi, "WEB_TIMEOUT", 60.0)
    monkeypatch.setattr(scraperapi, "MODEL_BUDGET", 15.0)
    assert scraperapi.budget_warning() == ""


def test_the_fetch_line_shows_the_budget(monkeypatch):
    monkeypatch.setattr(scraperapi, "API_KEY", "k")
    monkeypatch.setattr(scraperapi, "TIMEOUT", 40.0)
    monkeypatch.setattr("app.catalog_sources.base.allowed_by_robots",
                        lambda url, agent=None: True)

    def explode(*a, **k):
        raise urllib.error.URLError(TimeoutError("timed out"))

    monkeypatch.setattr(scraperapi.urllib.request, "urlopen", explode)
    trace.start()
    with pytest.raises(scraperapi_error()) as caught:
        scraperapi.ScraperApiFetcher()("https://partsouq.com/en/search/all?q=X")
    log = "\n".join(trace.lines())
    assert "תקציב 40ש" in log
    assert "לא נענה אחרי" in log
    assert "SCRAPERAPI_RENDER=0" in str(caught.value)


def scraperapi_error():
    from app.catalog_sources.base import FetchError

    return FetchError
