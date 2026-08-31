"""גרידת חלפים מ-Autodoc, בהפעלת מנהל האפליקציה.

הגריד עצמו הוא פרויקט Scrapy עצמאי שיושב ב-scraper/. כאן רק מה שנוגע
לאפליקציה: להפעיל אותו על מטרה אחת, לתרגם את מה שחזר לשפת הקטלוג,
ולתת לצנרת הקיימת לעשות את השאר.

למה תת-תהליך ולא ייבוא: Scrapy רץ על Twisted, ו-reactor שנסגר אינו
נפתח שוב באותו תהליך. worker של gunicorn שהריץ גריד אחד היה נשבר
בהרצה השנייה. תת-תהליך מת בסוף כל מטרה, וזה גם מה שמאפשר להגביל אותו
בזמן - הרצה תקועה נהרגת לפני שה-timeout של gunicorn הורג את הבקשה.

למה אותו אימות של הגילוי: מקור אחר, סכנה זהה. עמוד קטגוריה של דגם
מציג גם חלפים שאינם שלו, ולכן כל שורה שחוזרת מכאן עוברת ב-validate של
parts_discovery בדיוק כמו מועמד שהמודל החזיר. השמירה מסמנת אותה
כ"גריד Autodoc", כדי שאפשר יהיה לאתר ולמחוק בדיוק את מה שהגריד הכניס.

מחיר לא נשמר: המחיר בקטלוג הוא שכבה פרטית של ארגון (OrgPart), ומחיר
של חנות מקוונת אינו המחיר של המוסך.
"""
import json
import os
import subprocess
import sys
import tempfile
from importlib.util import find_spec
from pathlib import Path

from flask import current_app

from . import parts_discovery

SCRAPER_DIR = Path(__file__).resolve().parent.parent / "scraper"
SPIDER = "autodoc"

# ברירות מחדל למי שקורא בלי app context (סקריפט, בדיקה)
DEFAULT_TIMEOUT = 40
DEFAULT_MAX_ITEMS = 30


def available():
    """האם אפשר להריץ כאן גריד: Scrapy מותקן והפרויקט במקומו."""
    return find_spec("scrapy") is not None and (SCRAPER_DIR / "scrapy.cfg").is_file()


def _setting(key, fallback):
    """ערך מהגדרות האפליקציה, גם כשאין app context."""
    try:
        return current_app.config.get(key, fallback)
    except RuntimeError:
        return fallback


def _tail(text, lines=3, width=400):
    """סוף ה-stderr, שזה המקום שבו Scrapy כותב את מה שהשתבש."""
    rows = [row.strip() for row in (text or "").splitlines() if row.strip()]
    return " | ".join(rows[-lines:])[:width]


def run_spider(make, model, part_type, timeout=None, max_items=None, details=None):
    """מריץ מטרה אחת ומחזיר את השורות הגולמיות. מרים חריגה בכשל.

    הגריד כותב JSON לקובץ זמני במקום ל-stdout, כי ל-stdout הוא כותב גם
    דברים אחרים. הקובץ נמחק בסוף בכל מקרה.
    """
    if not available():
        raise RuntimeError(
            "Scrapy אינו מותקן בשרת, או שתיקיית scraper/ חסרה."
        )

    timeout = float(timeout or _setting("AUTODOC_TIMEOUT", DEFAULT_TIMEOUT))
    max_items = int(max_items or _setting("AUTODOC_MAX_ITEMS", DEFAULT_MAX_ITEMS))
    if details is None:
        details = bool(_setting("AUTODOC_FOLLOW_PRODUCT_PAGES", False))

    with tempfile.TemporaryDirectory() as folder:
        output = Path(folder) / "parts.json"
        command = [
            sys.executable, "-m", "scrapy", "crawl", SPIDER,
            "-a", f"make={make}",
            "-a", f"model={model}",
            "-a", f"part_type={part_type}",
            "-a", f"limit={max_items}",
            "-a", f"details={1 if details else 0}",
            "-O", str(output),
        ]
        environment = {
            **os.environ,
            "AUTODOC_MAX_ITEMS": str(max_items),
            # הגריד עוצר את עצמו לפני שאנחנו הורגים אותו, כדי שמה שכבר
            # נאסף ייכתב לקובץ במקום ללכת לאיבוד
            "AUTODOC_SPIDER_TIMEOUT": str(max(5.0, timeout - 5)),
            "PYTHONIOENCODING": "utf-8",
        }
        try:
            finished = subprocess.run(
                command, cwd=str(SCRAPER_DIR), env=environment, timeout=timeout,
                capture_output=True, text=True,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"הגריד לא סיים תוך {timeout:g} שניות.") from None
        except OSError as exc:
            raise RuntimeError(f"הפעלת הגריד נכשלה: {exc}") from None

        rows = _read_rows(output)

    if finished.returncode != 0:
        raise RuntimeError(
            _tail(finished.stderr) or f"הגריד יצא בקוד {finished.returncode}."
        )
    # אפס שורות זה מצב לגיטימי (אין את הדגם באתר), אבל אם הגריד גם כתב
    # שגיאה - זו הסיבה, והיא שווה יותר מ"לא נמצא כלום"
    if not rows and finished.stderr.strip():
        raise RuntimeError(_tail(finished.stderr))
    return rows


def _read_rows(path):
    """קורא את פלט הגריד. קובץ חסר או פגום = אין שורות."""
    try:
        text = Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return []
    if not text:
        return []
    try:
        loaded = json.loads(text)
    except ValueError:
        return []
    return loaded if isinstance(loaded, list) else []


def to_candidates(rows, make, model, part_type):
    """שורות הגריד -> מועמדים בפורמט שהאימות של הגילוי מכיר.

    הכותרת נכנסת ל-note בכוונה: זה מה שהשומר של היצרן הזר קורא. חלף
    שכותרתו מזכירה יצרן רכב אחר מזה שביקשנו הוא בדיוק המקרה שהתגלה
    בפועל בעמודי קטלוג, וכאן הוא נפסל.

    confidence הוא "high" כי הכתובת נבנתה לדגם שביקשנו - האתר עצמו
    אמר שאלה החלפים שלו. האימות הוא זה שיחליט אם להסכים.
    """
    candidates = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        oe_numbers = row.get("oe_numbers") or []
        candidates.append({
            "part_number": str(row.get("part_number") or "").strip(),
            "manufacturer": str(row.get("manufacturer") or "").strip(),
            "confidence": "high",
            "note": str(row.get("title") or "").strip(),
            "oe_number": str(oe_numbers[0] if oe_numbers else "").strip(),
            "oe_brand": "",
            "source_url": str(row.get("url") or row.get("listing_url") or "").strip(),
            "make": make,
            "model": model,
            "part_type": part_type,
        })
    return candidates


def searcher(make, model, part_type):
    """החתימה ש-parts_discovery.run_step מצפה לה, מגובה בגריד."""
    return to_candidates(
        run_spider(make, model, part_type), make, model, part_type
    )


# --------------------------------------------------------------------------
# עבודות. אותה טבלה ואותה צנרת של הגילוי, עם מקור "autodoc"
# --------------------------------------------------------------------------

SOURCE = parts_discovery.AUTODOC


def active_job():
    return parts_discovery.active_job(SOURCE)


def latest_job():
    return parts_discovery.latest_job(SOURCE)


def start_job(targets, user_id=None):
    return parts_discovery.start_job(targets, user_id=user_id, source=SOURCE)


def cancel_job(job):
    return parts_discovery.cancel_job(job)


def run_step(job, spider=None):
    """מטרה אחת. spider ניתן להחלפה כדי שהבדיקות לא יצאו לרשת."""
    return parts_discovery.run_step(job, searcher=spider or searcher)
