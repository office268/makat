"""קציר מק"טים לפי סבבים - הערוץ שנמדד כיעיל ביותר.

זה המימוש של §2א בסקיל ``oem-part-numbers``: **עמוד קטגוריה ברמת
יצרן**. הכתובת ``{אתר}/oem-{יצרן}-{חלק}.html`` בלי שם דגם מחזירה את
החלק הזה לכל הדגמים של אותו יצרן, ו-``?page=2``/``?page=3`` מחזירים
שורות *אחרות*. שליפה אחת ≈ 18 מק"טים חדשים, מהם ≈16 מקבלים סוג חלק.

**למה סבבים ולא "תריץ עד שתסיים".** קציר עולה כסף - בקשת רשת וקריאת
מודל לכל שליפה - והתשואה יורדת ככל שקטגוריה כבר נגעה ביצרן. סבב הוא
יחידת החלטה: מגדירים כמה מק"טים חדשים רוצים ממנו, הוא נעצר כשהגיע
לשם או כשמטרותיו נגמרו, ואז אפשר להסתכל על מה שנכנס לפני שממשיכים.
שני המספרים שהמשתמש נותן - כמה בסבב וכמה סבבים - הם התקציב כולו.

**מה נלמד ומקובע כאן כדי לא לגשש שוב:**

* ``KNOWN_404`` - צירופי אתר×קטגוריה שנמדדו כלא קיימים. גישוש חוזר
  בהם הוא שליפה מבוזבזת, והם לא מעטים: ``starter_motor`` אצל ב.מ.וו,
  מאזדה ופולקסווגן, ``thermostat`` אצל ג'י.אם (עמוד ריק, לא 404).
* **עמוד שמחזיר את עצמו** - אאודי ופולקסווגן החזירו ב-``?page=2``
  בדיוק את עשרים השורות של עמוד 1, בלי 404 ובלי סימן. לכן המק"ט
  הראשון של כל עמוד נשמר, ועמוד שחוזר על קודמו נעצר ולא נכתב.
* **קטגוריה בתולית היא הווקטור החזק** - סוג חלק שלא נגעו בו כלל פותח
  את כל היצרנים בבת אחת. לכן התכנון מתחיל מהפערים בקטלוג, לא מרשימה
  קבועה.
"""
import json
import math
import os
import re
from datetime import datetime, timezone

from . import parts_discovery
from .models import Part, db
from .taxonomy import PART_TYPES, type_name

# --------------------------------------------------------------------------
# מה שנמדד בסקיל, כנתונים
# --------------------------------------------------------------------------

# (מפתח, מארח, היצרן כפי שהוא בכתובת, האם הכתובת נגמרת ב-.html)
SITES = [
    ("toyota", "toyotapartsdeal.com", "toyota", True),
    ("lexus", "lexuspartsnow.com", "lexus", True),
    ("honda", "hondapartsnow.com", "honda", True),
    ("acura", "acurapartswarehouse.com", "acura", True),
    ("nissan", "nissanpartsdeal.com", "nissan", True),
    ("infiniti", "infinitipartsdeal.com", "infiniti", True),
    ("subaru", "subarupartsdeal.com", "subaru", True),
    # מאזדה היא היחידה בלי הסיומת. נמדד.
    ("mazda", "mazdapartsnow.com", "mazda", False),
    ("hyundai", "hyundaipartsdeal.com", "hyundai", True),
    ("kia", "kiapartsnow.com", "kia", True),
    ("bmw", "bmwpartsdeal.com", "bmw", True),
    ("audi", "audipartsgiant.com", "audi", True),
    # אצל פולקסווגן היצרן בכתובת הוא השם המלא ולא ה-slug של האתר.
    ("volkswagen", "vwpartsgiant.com", "volkswagen", True),
    ("ford", "fordpartsgiant.com", "ford", True),
    # ג'י.אם ומופאר הם מטריות: כל שורה נושאת את המותג שלה.
    ("gm", "gmpartsgiant.com", "gm", True),
    ("mopar", "moparpartsgiant.com", "mopar", True),
]

UMBRELLA_SITES = {"gm", "mopar"}

# תחילית הקטגוריה באתר -> מפתח סוג החלק אצלנו. רק תחיליות שנמדדו.
PREFIX_TYPES = {
    "oil_filter": "oil_filter",
    "air_filter": "air_filter",
    "cabin_air_filter": "cabin_filter",
    "fuel_filter": "fuel_filter",
    "timing_belt": "timing_belt",
    "water_pump": "water_pump",
    "thermostat": "thermostat",
    "alternator": "alternator",
    "starter_motor": "starter",
    "spark_plug": "spark_plug",
    "ignition_coil": "ignition_coil",
    "wiper_blade": "wiper_blade",
    "a_c_compressor": "ac_compressor",
    "oxygen_sensor": "oxygen_sensor",
    "fuel_pump": "fuel_pump",
    "catalytic_converter": "catalytic_converter",
    "control_arm": "control_arm",
    "ball_joint": "ball_joint",
    "sway_bar_link": "stabilizer_link",
    "wheel_bearing": "wheel_bearing",
    "brake_caliper": "brake_caliper",
    "engine_mount": "engine_mount",
    "cooling_fan_assembly": "radiator_fan",
    "coil_spring": "coil_spring",
}

# קטגוריות שהטקסונומיה שלנו מפצלת לצדדים, והאתר לא. הצד נלקח מהשם
# בלבד; שורה שלא נוקבת בצד - או שנוקבת בשניהם - נדחית ולא מנוחשת.
SIDED_PREFIXES = {
    "brake_pad_set": ("brake_pads_front", "brake_pads_rear"),
    "brake_disc": ("brake_disc_front", "brake_disc_rear"),
    "shock_absorber": ("shock_absorber_front", "shock_absorber_rear"),
}

# צירופים שנמדדו כלא קיימים. גישוש חוזר בהם הוא שליפה מבוזבזת.
KNOWN_404 = {
    ("ford", "brake_pad_set"), ("mopar", "brake_pad_set"),
    ("ford", "oxygen_sensor"),
    ("bmw", "brake_pad_set"), ("bmw", "starter_motor"),
    ("mopar", "spark_plug"),
    ("nissan", "cooling_fan_assembly"), ("gm", "cooling_fan_assembly"),
    ("gm", "brake_caliper"),
    # ג'י.אם מחזיר כאן עמוד ריק ולא 404 - אותה תוצאה, בלי סימן.
    ("gm", "thermostat"),
    ("mazda", "starter_motor"), ("volkswagen", "starter_motor"),
    ("honda", "coil_spring"),
    ("subaru", "engine_mount"),
}

# עמודי המשך שנמדדו כלא קיימים, בנוסף לכלל הכללי ש-``?page=4`` הוא 404.
KNOWN_404_PAGES = {
    ("kia", "timing_belt", 2), ("mazda", "timing_belt", 2),
}

MAX_PAGE = 3

# התשואה שנמדדה, ולפיה מתוכנן מספר השליפות בסבב. קטגוריה בתולית נותנת
# יותר וקטגוריה שנגעו בה פחות - זו הערכה לתכנון, לא הבטחה.
YIELD_PER_FETCH = int(os.environ.get("HARVEST_YIELD", 18))
# תקרה קשיחה לשליפות בסבב, כדי שמספר גדול בטופס לא ייצור חשבון פתוח.
MAX_FETCHES_PER_ROUND = int(os.environ.get("HARVEST_MAX_FETCHES", 40))
# ומרצפה, כדי שבקשה קטנה לא תיפול על שליפה אחת שבמקרה לא החזירה כלום.
MIN_FETCHES_PER_ROUND = int(os.environ.get("HARVEST_MIN_FETCHES", 5))
# פי כמה מההערכה מותר להוציא לפני שנעצרים. התשואה שנמדדה אופטימית -
# היא נמדדה על קטגוריות בתוליות - ולכן צריך מרווח, אבל לא בלי גבול.
FETCH_SLACK = int(os.environ.get("HARVEST_FETCH_SLACK", 4))
MAX_ROUNDS = int(os.environ.get("HARVEST_MAX_ROUNDS", 20))
MAX_PER_ROUND = int(os.environ.get("HARVEST_MAX_PER_ROUND", 500))

SOURCE_NOTE = "נוסף בקציר מעמוד קטגוריה של קטלוג דילר. מקור לא רשמי."
SOURCE_MARK = "נוסף בקציר מעמוד קטגוריה"


def _now():
    return datetime.now(timezone.utc)


def build_url(site_key, prefix, page=1):
    """הכתובת של עמוד קטגוריה ברמת יצרן."""
    for key, host, make_token, dot_html in SITES:
        if key != site_key:
            continue
        suffix = ".html" if dot_html else ""
        url = f"https://{host}/oem-{make_token}-{prefix}{suffix}"
        return f"{url}?page={page}" if page > 1 else url
    return ""


def site_label(site_key):
    return next((host for key, host, _m, _h in SITES if key == site_key), site_key)


# --------------------------------------------------------------------------
# תכנון סבב
# --------------------------------------------------------------------------

def coverage():
    """כמה מק"טים יש בקטלוג לכל סוג חלק. זה מה שמגדיר "בתולי"."""
    rows = (
        db.session.query(Part.part_type, db.func.count(Part.id))
        .group_by(Part.part_type)
        .all()
    )
    return {part_type: count for part_type, count in rows if part_type}


def prefix_types(prefix):
    """סוגי החלק שקטגוריה יכולה לייצר."""
    if prefix in SIDED_PREFIXES:
        return list(SIDED_PREFIXES[prefix])
    return [PREFIX_TYPES[prefix]]


def ranked_prefixes(counts=None):
    """הקטגוריות לפי הפער: הדל ביותר קודם.

    זה הלקח המרכזי של הסקיל - סוג חלק שלא נגעו בו כלל שווה יותר מדגם
    נוסף באותו סוג, כי הוא פותח את כל היצרנים בבת אחת.
    """
    counts = coverage() if counts is None else counts
    prefixes = list(PREFIX_TYPES) + list(SIDED_PREFIXES)
    return sorted(
        prefixes,
        key=lambda prefix: (
            min(counts.get(t, 0) for t in prefix_types(prefix)),
            prefix,
        ),
    )


def estimate_fetches(wanted):
    """כמה שליפות *צפויות* כדי להגיע למספר שביקשו, לפי התשואה שנמדדה.

    הערכה לתצוגה מקדימה בלבד. היא אינה התקציב: המספר שהמשתמש נתן הוא
    מק"טים, לא שליפות, ולכן הסבב ממשיך עד שהגיע אליו - גם אם לקח יותר
    שליפות מהצפוי - ונעצר מוקדם כשלקח פחות.
    """
    return max(1, math.ceil(wanted / max(1, YIELD_PER_FETCH)))


def fetch_budget(wanted):
    """תקרת השליפות לסבב - כמה מותר לשלם כדי להגיע למספר שביקשו.

    התקרה נגזרת מהבקשה ולא קבועה, כי בקשה של שלושה מק"טים לא אמורה
    לשלם ארבעים קריאות מודל אם האתרים לא מחזירים כלום. מרווח של פי
    ``FETCH_SLACK`` על ההערכה, רצפה שמאפשרת גם לבקשה קטנה כמה נסיונות,
    ותקרה קשיחה למעלה.
    """
    room = estimate_fetches(wanted) * max(1, FETCH_SLACK)
    return max(MIN_FETCHES_PER_ROUND, min(MAX_FETCHES_PER_ROUND, room))


def plan_round(wanted, counts=None, done=(), fetches=None):
    """המטרות לסבב אחד: [[מפתח אתר, תחילית, עמוד], ...].

    הרשימה מסודרת מהטוב לפחות טוב ומוגבלת ב-``fetch_budget``, שהיא
    **תקרת עלות ולא תוכנית**: הסבב עוצר על מספר המק"טים שביקשו,
    והמטרות שנשארו פשוט לא נשלפות. הבחנה שנראית טכנית והיא לא - הכפתור
    שהמשתמש קיבל הוא "כמה מק"טים", ולכן סבב שנעצר על ארבעה כשביקשו
    חמישה, בזמן שיש עוד מטרות, הוא סבב שלא עשה את מה שביקשו ממנו.

    ``done`` הוא מה שכבר נשלף בסבבים קודמים של אותה עבודה, כדי שסבב
    שני לא יחזור על אותם עמודים אלא ימשיך לעמוד הבא.
    """
    counts = coverage() if counts is None else counts
    budget = fetches or fetch_budget(wanted)
    seen = {tuple(item) for item in done}
    targets = []

    # עמוד 1 של הקטגוריות הדלות קודם, ואז עמודי ההמשך. קטגוריה שכבר
    # נקצרה מחזירה בעמוד 1 אפס עד ארבעה חדשים - שם הרווח הוא בעמוד 2.
    for page in range(1, MAX_PAGE + 1):
        for prefix in ranked_prefixes(counts):
            for site_key, _host, _make, _html in SITES:
                if len(targets) >= budget:
                    return targets
                if (site_key, prefix) in KNOWN_404:
                    continue
                if (site_key, prefix, page) in KNOWN_404_PAGES:
                    continue
                target = (site_key, prefix, page)
                if target in seen:
                    continue
                targets.append([site_key, prefix, page])
    return targets


# --------------------------------------------------------------------------
# קריאת העמוד
# --------------------------------------------------------------------------

def build_prompt(site_key, prefix, page_text, url):
    """ההנחיה: לקרוא עמוד קטגוריה ולהחזיר רק שורות שנכתבו בו."""
    umbrella = site_key in UMBRELLA_SITES
    brand_rule = (
        "- העמוד הזה מרכז כמה מותגים. בכל שורה קח את המותג שכתוב בה "
        "(שברולט/GMC/ביואיק/קאדילק · ג'יפ/דודג'/קרייזלר/ראם), ואם שורה "
        "מונה כמה מותגים - החזר שורה נפרדת לכל מותג, בלי שנים.\n"
        if umbrella
        else ""
    )
    return f"""לפניך תוכן של עמוד קטגוריה מקטלוג דילר: כל החלקים מסוג אחד, לכל דגמי היצרן.

הקטגוריה: {prefix}
כתובת: {url}

תוכן העמוד:
---
{page_text}
---

החזר JSON בלבד:
{{"parts": [
  {{"part_number": "המק\\"ט בדיוק כפי שמופיע",
    "brand": "יצרן הרכב של השורה",
    "model": "שם הדגם כפי שמופיע, בלי שם החלק ובלי שם היצרן",
    "year_from": מספר או null, "year_to": מספר או null,
    "name": "שם החלק כפי שמופיע בשורה, מילה במילה"}}
]}}

כללים מחייבים:
- שורה נכתבת רק אם המק"ט הופיע בעמוד **מילה במילה**. אל תשלים ספרות,
  אל תנחש סיומת ואל תרחיב טווח.
- רק שורה שיש בה **גם דגם וגם שנים**. שורה שכתוב בה "Multiple models"
  במקום דגם - דלג עליה.
{brand_rule}- ``model`` הוא שם הדגם בלבד. שם החלק ושם היצרן נדבקים לו בעמודים
  האלה ("EXPLORER FORD", "A4 THERMOSTAT HOUSING") - הסר אותם.
- ``name`` נשאר בדיוק כפי שהעמוד כתב אותו, כולל שגיאות כתיב.
- שני טווחי שנים בתא אחד ("2017-2022, 2005-2010") = החזר null בשניהם.
- עד 30 שורות.
"""


def read_page(site_key, prefix, page, fetcher=None, client=None):
    """מביא עמוד קטגוריה ומחזיר (שורות, כתובת). מרים חריגה בכשל הבאה."""
    from .catalog_sources.base import ask_model, condense, default_fetcher

    url = build_url(site_key, prefix, page)
    get_page = fetcher or default_fetcher()
    html = get_page(url)
    answer = ask_model(
        build_prompt(site_key, prefix, condense(html, url), url), client=client
    )
    return (answer.get("parts") or []), url


_FRONT = re.compile(r"\b(front|frt|fr\.)\b", re.I)
_REAR = re.compile(r"\b(rear|rr\.?|back)\b", re.I)


def resolve_type(prefix, name):
    """סוג החלק לשורה. ריק כשהצד לא נכתב, או כשנכתבו שניים.

    זה הכלל שעלה בסקיל עשרים מק"טי ליבה, והיה שווה אותם: שם שנוקב גם
    בקדמי וגם באחורי אינו מוסר צד - הוא מוסר שניים.
    """
    if prefix not in SIDED_PREFIXES:
        return PREFIX_TYPES.get(prefix)
    front_key, rear_key = SIDED_PREFIXES[prefix]
    text = name or ""
    has_front, has_rear = bool(_FRONT.search(text)), bool(_REAR.search(text))
    if has_front and not has_rear:
        return front_key
    if has_rear and not has_front:
        return rear_key
    return None


def to_rows(raw_parts, prefix, url):
    """שורות המודל -> שורות ל-``validate``, אחרי הכללים שאינם מודל."""
    rows, skipped = [], []
    for raw in raw_parts or []:
        if not isinstance(raw, dict):
            continue
        number = str(raw.get("part_number") or "").strip()
        brand = str(raw.get("brand") or "").strip()
        model = str(raw.get("model") or "").strip()
        name = str(raw.get("name") or "").strip()
        if not (number and brand and model):
            skipped.append((number or "?", "חסר מק\"ט, יצרן או דגם"))
            continue
        part_type = resolve_type(prefix, name)
        if not part_type:
            skipped.append((number, f"הצד לא נכתב בשם: {name[:40]}"))
            continue
        rows.append({
            "part_number": number,
            "manufacturer": brand,
            "make": brand,
            "model": model,
            "part_type": part_type,
            "name": name,
            "year_from": raw.get("year_from"),
            "year_to": raw.get("year_to"),
            "source_url": url,
            # עמוד קטגוריה הוא ראיה חזקה: היצרן עצמו שם את המק"ט בעמוד
            # הזה. לכן השורה נכנסת לאימות עם ביטחון גבוה.
            "confidence": "high",
        })
    return rows, skipped


# --------------------------------------------------------------------------
# העבודה: סבבים, ושליפה אחת לכל בקשת HTTP
# --------------------------------------------------------------------------

class HarvestJob(db.Model):
    """קציר אחד: כמה סבבים, וכמה מק"טים חדשים רוצים מכל סבב.

    אותו דפוס כמו שאר העבודות הארוכות כאן - שליפה אחת לכל בקשה,
    וההתקדמות ב-DB - כי בקשת רשת ועוד קריאת מודל לא נכנסות בתקציב
    של gunicorn, ושני workers צריכים לראות את אותו מצב.
    """

    __tablename__ = "harvest_jobs"

    RUNNING, DONE, FAILED, CANCELLED = "running", "done", "failed", "cancelled"
    STATUS_LABELS = {RUNNING: "קוצר", DONE: "הושלם",
                     FAILED: "נכשל", CANCELLED: "בוטל"}

    id = db.Column(db.Integer, primary_key=True)
    status = db.Column(db.String(20), default=RUNNING, nullable=False, index=True)

    wanted_per_round = db.Column(db.Integer, nullable=False)
    rounds = db.Column(db.Integer, nullable=False)
    round_index = db.Column(db.Integer, default=0, nullable=False)

    targets = db.Column(db.Text, nullable=False)   # JSON - מטרות הסבב הנוכחי
    visited = db.Column(db.Text, default="[]")     # JSON - מה שכבר נשלף
    cursor = db.Column(db.Integer, default=0, nullable=False)

    added_round = db.Column(db.Integer, default=0, nullable=False)
    added_total = db.Column(db.Integer, default=0, nullable=False)
    updated_total = db.Column(db.Integer, default=0, nullable=False)
    rejected_total = db.Column(db.Integer, default=0, nullable=False)
    fetches = db.Column(db.Integer, default=0, nullable=False)

    # המק"ט הראשון של כל עמוד, לתפיסת עמוד שמחזיר את קודמו
    seen_first = db.Column(db.Text, default="{}")
    log = db.Column(db.Text, default="")
    error = db.Column(db.Text)

    started_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    started_at = db.Column(db.DateTime, default=_now)
    updated_at = db.Column(db.DateTime, default=_now)
    finished_at = db.Column(db.DateTime)

    def _load(self, field, fallback):
        try:
            return json.loads(getattr(self, field) or "") or fallback
        except ValueError:
            return fallback

    @property
    def target_list(self):
        return self._load("targets", [])

    @property
    def visited_list(self):
        return self._load("visited", [])

    @property
    def first_numbers(self):
        return self._load("seen_first", {})

    @property
    def is_running(self):
        return self.status == self.RUNNING

    @property
    def fetch_budget(self):
        return fetch_budget(self.wanted_per_round)

    @property
    def status_label(self):
        return self.STATUS_LABELS.get(self.status, self.status)

    @property
    def progress_pct(self):
        """התקדמות על פני כל הסבבים, לא רק הנוכחי."""
        total = max(1, self.rounds * max(1, self.wanted_per_round))
        done = (self.round_index * self.wanted_per_round) + self.added_round
        return min(100, round(done * 100 / total))

    def to_dict(self):
        return {
            "id": self.id,
            "status": self.status,
            "status_label": self.status_label,
            "is_running": self.is_running,
            "round": self.round_index + 1,
            "rounds": self.rounds,
            "wanted_per_round": self.wanted_per_round,
            "cursor": self.cursor,
            "targets": len(self.target_list),
            "added_round": self.added_round,
            "added_total": self.added_total,
            "updated_total": self.updated_total,
            "rejected_total": self.rejected_total,
            "fetches": self.fetches,
            "fetch_budget": self.fetch_budget,
            "progress_pct": self.progress_pct,
            "error": self.error,
            "log": (self.log or "").strip().split("\n")[-40:] if self.log else [],
        }


def available():
    """האם אפשר לקצור: צריך במה להביא דפים ומי שיקרא אותם."""
    from .catalog_sources.base import fetcher_available, parser_available

    return parser_available() and fetcher_available(needs_js=False)


def active_job():
    return (
        HarvestJob.query.filter_by(status=HarvestJob.RUNNING)
        .order_by(HarvestJob.id.desc()).first()
    )


def latest_job():
    return HarvestJob.query.order_by(HarvestJob.id.desc()).first()


def start_job(wanted_per_round, rounds, user_id=None):
    """פותח קציר. מרים ``ValueError`` עם סיבה קריאה כשהמספרים לא סבירים."""
    try:
        wanted = int(wanted_per_round)
        count = int(rounds)
    except (TypeError, ValueError):
        raise ValueError("שני השדות חייבים להיות מספרים.")
    if wanted < 1 or count < 1:
        raise ValueError("צריך לפחות מק\"ט אחד בסבב אחד.")
    if wanted > MAX_PER_ROUND:
        raise ValueError(f"עד {MAX_PER_ROUND} מק\"טים בסבב.")
    if count > MAX_ROUNDS:
        raise ValueError(f"עד {MAX_ROUNDS} סבבים.")
    if active_job() is not None:
        raise ValueError("כבר רץ קציר. חכה שיסתיים או בטל אותו.")

    job = HarvestJob(
        wanted_per_round=wanted,
        rounds=count,
        targets=json.dumps(plan_round(wanted), ensure_ascii=False),
        started_by_id=user_id,
    )
    db.session.add(job)
    db.session.commit()
    return job


def cancel_job(job):
    if job is not None and job.is_running:
        job.status = HarvestJob.CANCELLED
        job.finished_at = _now()
        db.session.commit()
    return job


def _note(job, line):
    job.log = ((job.log or "") + line + "\n")[-8000:]


def _next_round(job):
    """סוגר את הסבב הנוכחי ופותח את הבא, או מסיים."""
    short = job.added_round < job.wanted_per_round
    _note(job, f"— סבב {job.round_index + 1} נסגר: {job.added_round} מק\"טים חדשים")
    # סבב שנסגר מתחת למספר שביקשו נסגר על תקרת השליפות, לא על היעד.
    # בלי המשפט הזה נראה כאילו זה כל מה שיש באתרים, וזה לא מה שקרה.
    if short:
        budget = job.fetch_budget
        reason = (
            "נגמרו המטרות לסבב"
            if len(job.target_list) < budget
            else f"נעצר על תקרת {budget} השליפות לסבב"
        )
        _note(
            job,
            f"    {reason}, לא על {job.wanted_per_round} המק\"טים שביקשת.",
        )
    if job.round_index + 1 >= job.rounds:
        job.status = HarvestJob.DONE
        job.finished_at = _now()
        db.session.commit()
        return job
    job.round_index += 1
    job.added_round = 0
    job.cursor = 0
    job.targets = json.dumps(
        plan_round(job.wanted_per_round, done=job.visited_list), ensure_ascii=False
    )
    job.updated_at = _now()
    if not job.target_list:
        _note(job, "אין עוד מטרות שלא נשלפו. הקציר מסתיים מוקדם.")
        job.status = HarvestJob.DONE
        job.finished_at = _now()
    db.session.commit()
    return job


def run_step(job, reader=None):
    """שליפה אחת: עמוד, אימות, כתיבה. מחזיר את העבודה."""
    if not job.is_running:
        return job
    targets = job.target_list
    if job.added_round >= job.wanted_per_round or job.cursor >= len(targets):
        return _next_round(job)

    site_key, prefix, page = targets[job.cursor]
    url = build_url(site_key, prefix, page)
    header = f"{site_label(site_key)} · {prefix} · עמוד {page}"

    try:
        raw_parts, url = (reader or read_page)(site_key, prefix, page)
    except Exception as exc:
        job.error = f"{header}: {exc}"
        _note(job, f"{header}: {exc}")
        job.cursor += 1
        job.fetches += 1
        job.updated_at = _now()
        db.session.commit()
        return job

    job.fetches += 1
    visited = job.visited_list
    visited.append([site_key, prefix, page])
    job.visited = json.dumps(visited, ensure_ascii=False)

    rows, skipped = to_rows(raw_parts, prefix, url)

    # עמוד שמחזיר את קודמו: אאודי ופולקסווגן עשו את זה בלי 404 ובלי
    # סימן. בלי הבדיקה הזו מכפילים עשרים שורות ומאבדים שליפה.
    firsts = job.first_numbers
    key = f"{site_key}:{prefix}"
    first = rows[0]["part_number"] if rows else None
    if first and firsts.get(key) == first:
        _note(job, f"{header}: חוזר על העמוד הקודם - נעצר בקטגוריה הזו")
        # ולא רק העמוד הזה: אם עמוד 2 החזיר את עמוד 1, גם עמוד 3 יחזיר
        # אותו. הורדת שאר העמודים של הקטגוריה מהתוכנית חוסכת שליפות
        # שכבר ידוע מה יחזור מהן.
        job.targets = json.dumps(
            targets[: job.cursor + 1]
            + [t for t in targets[job.cursor + 1:]
               if (t[0], t[1]) != (site_key, prefix)],
            ensure_ascii=False,
        )
        job.cursor += 1
        job.updated_at = _now()
        db.session.commit()
        return job
    if first:
        firsts[key] = first
        job.seen_first = json.dumps(firsts, ensure_ascii=False)

    created = updated = rejected = 0
    for row in rows:
        accepted, bad = parts_discovery.validate(
            [row], row["make"], row["model"], row["part_type"]
        )
        rejected += len(bad)
        for item in accepted:
            item["year"] = row.get("year_from")
            item["year_from"] = row.get("year_from")
            item["year_to"] = row.get("year_to")
            item["name"] = row.get("name")
        if accepted:
            made, upd = parts_discovery.save(
                accepted, source_note=SOURCE_NOTE, source_mark=SOURCE_MARK
            )
            created += made
            updated += upd

    job.added_round += created
    job.added_total += created
    job.updated_total += updated
    job.rejected_total += rejected + len(skipped)
    _note(
        job,
        f"{header}: נוספו {created}, עודכנו {updated}, "
        f"נפסלו {rejected + len(skipped)}",
    )
    for number, reason in skipped[:3]:
        _note(job, f"    ✗ {number} — {reason}")

    job.error = None
    job.cursor += 1
    job.updated_at = _now()
    db.session.commit()

    if job.added_round >= job.wanted_per_round or job.cursor >= len(targets):
        return _next_round(job)
    return job
