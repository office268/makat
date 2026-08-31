"""פירוק ה-HTML לשורות. פונקציות טהורות, בלי רשת ובלי Scrapy.

הפרדה מכוונת: מבנה העמוד של חנות מקוונת משתנה, וזה החלק שיישבר ראשון.
כשהוא כאן - בפונקציה שמקבלת מחרוזת ומחזירה רשימה - אפשר לשמור עמוד
אמיתי כקובץ, להריץ עליו בדיקה, ולתקן בלי לצאת לרשת ובלי להריץ גריד.

שני מסלולים, לפי סדר האמינות:
  1. JSON-LD. האתר פולט Product מובנה עבור מנועי חיפוש, ושם המק"ט
     והיצרן כתובים במפורש. זה המסלול המדויק.
  2. סלקטורים על ה-HTML, כשאין JSON-LD. ניחוש מושכל, ולכן רשימת
     הסלקטורים ניתנת לדריסה מהסביבה (AUTODOC_SELECTORS) - עמוד שהשתנה
     מתוקן בלי פריסה מחדש.
"""
import json
import os
import re
from urllib.parse import urljoin

from parsel import Selector

# ברירת המחדל של הסלקטורים. כל מפתח הוא רשימה, והראשון שמחזיר טקסט מנצח.
SELECTORS = {
    "tile": [
        ".listing-item", "[data-product-id]", ".product-item",
        "article.product", "li.product",
    ],
    "brand": [
        "[data-brand]::attr(data-brand)", ".listing-item__manufacturer::text",
        ".product-brand::text", ".brand::text", "[itemprop='brand']::text",
    ],
    "number": [
        "[data-article-number]::attr(data-article-number)",
        ".listing-item__article-number::text", ".product-number::text",
        "[itemprop='sku']::text", ".article-number::text",
    ],
    "title": [
        ".listing-item__name::text", ".product-title::text",
        "[itemprop='name']::text", "a::attr(title)",
    ],
    "price": [
        "[data-price]::attr(data-price)", ".listing-item__price::text",
        ".product-price::text", "[itemprop='price']::attr(content)",
        ".price::text",
    ],
    "url": ["a::attr(href)"],
    "image": ["img::attr(data-src)", "img::attr(src)"],
    "oe": [
        "[data-oe-number]::attr(data-oe-number)", ".oe-numbers li::text",
        ".oem-numbers li::text", ".product-oe-number::text",
    ],
}

# "מספר פריט: 1234", "Article number 1234" - כשאין סלקטור שתופס, הטקסט תופס
NUMBER_LABELS = re.compile(
    r"(?:מספר\s*פריט|מק[\"״']?ט|Article\s*(?:number|No)|Art\.?\s*No\.?|OEM?)"
    r"\s*[:：]?\s*([A-Za-z0-9][A-Za-z0-9\-./]{2,30})",
    re.IGNORECASE,
)
OE_LABELS = re.compile(
    r"(?:מספר[יי]?\s*OE[M]?|OE[M]?\s*(?:numbers?|Nummer))\s*[:：]?\s*(.{0,200})",
    re.IGNORECASE | re.DOTALL,
)
OE_NUMBER = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-./]{4,30}")


def selectors(key):
    """רשימת הסלקטורים למפתח, אחרי דריסה מהסביבה."""
    raw = os.environ.get("AUTODOC_SELECTORS", "").strip()
    if raw:
        try:
            override = json.loads(raw)
        except ValueError:
            override = {}
        if isinstance(override, dict) and key in override:
            value = override[key]
            return value if isinstance(value, list) else [value]
    return SELECTORS.get(key, [])


def _first(node, key):
    """הערך הראשון שאחד מהסלקטורים מחזיר, נקי מרווחים."""
    for query in selectors(key):
        value = node.css(query).get()
        if value and value.strip():
            return value.strip()
    return ""


def _number(text):
    """מחיר מתוך טקסט: '₪ 1,299.90' -> 1299.9. None כשאין."""
    match = re.search(r"\d[\d.,]*", (text or "").replace("\xa0", " "))
    if not match:
        return None
    raw = match.group(0)
    # "1.299,90" (אירופי) מול "1,299.90" - הסימן האחרון הוא הנקודה העשרונית
    if "," in raw and "." in raw:
        raw = (raw.replace(".", "").replace(",", ".")
               if raw.rfind(",") > raw.rfind(".") else raw.replace(",", ""))
    elif "," in raw:
        raw = raw.replace(",", ".") if len(raw.split(",")[-1]) == 2 else raw.replace(",", "")
    try:
        return float(raw)
    except ValueError:
        return None


def _absolute(href, page_url):
    """כתובת יחסית -> מלאה, לפי העמוד שממנו נלקחה."""
    if not href:
        return page_url
    if href.startswith("http"):
        return href
    return urljoin(page_url, href) if page_url else href


# ---------------------------------------------------------------------------
# JSON-LD
# ---------------------------------------------------------------------------

def _ld_blocks(selector):
    """כל אובייקטי ה-JSON-LD בעמוד, שטוחים. בלוק פגום מדולג."""
    found = []
    for raw in selector.css('script[type="application/ld+json"]::text').getall():
        try:
            loaded = json.loads(raw)
        except ValueError:
            continue
        queue = loaded if isinstance(loaded, list) else [loaded]
        while queue:
            node = queue.pop(0)
            if not isinstance(node, dict):
                continue
            found.append(node)
            for key in ("@graph", "itemListElement", "item", "mainEntity"):
                value = node.get(key)
                if isinstance(value, list):
                    queue.extend(value)
                elif isinstance(value, dict):
                    queue.append(value)
    return found


def _is_product(node):
    types = node.get("@type") or ""
    types = types if isinstance(types, list) else [types]
    return any(str(t).lower() == "product" for t in types)


def _text_of(value):
    """שם מתוך ערך שיכול להיות מחרוזת או אובייקט ({'name': ...})."""
    if isinstance(value, dict):
        return str(value.get("name") or "").strip()
    return str(value or "").strip()


def _offer(node):
    """(מחיר, מטבע) מתוך offers, שהוא אובייקט או רשימה."""
    offers = node.get("offers")
    if isinstance(offers, list):
        offers = offers[0] if offers else None
    if not isinstance(offers, dict):
        return None, ""
    return _number(str(offers.get("price") or "")), str(
        offers.get("priceCurrency") or ""
    ).strip()


def _image_of(node):
    """כתובת התמונה. schema.org מתיר מחרוזת, אובייקט או רשימה."""
    image = node.get("image")
    if isinstance(image, list):
        image = image[0] if image else ""
    return _text_of(image)


def _from_ld(node, page_url=""):
    price, currency = _offer(node)
    return {
        "part_number": str(node.get("sku") or node.get("mpn") or "").strip(),
        "manufacturer": _text_of(node.get("brand")),
        "title": str(node.get("name") or "").strip(),
        "price": price,
        "currency": currency,
        "url": _absolute(str(node.get("url") or "").strip(), page_url),
        "image_url": _image_of(node),
        "oe_numbers": [],
        "source": "json-ld",
    }


# ---------------------------------------------------------------------------
# עמוד קטגוריה
# ---------------------------------------------------------------------------

def parse_listing(html, page_url=""):
    """שורות מעמוד קטגוריה. רשימה ריקה = לא נמצא כלום, וזה לא חריגה."""
    selector = Selector(text=html or "")

    rows = [
        _from_ld(node, page_url)
        for node in _ld_blocks(selector)
        if _is_product(node)
    ]
    if rows:
        return [row for row in rows if row["part_number"]]

    for query in selectors("tile"):
        tiles = selector.css(query)
        if tiles:
            return [row for row in (_from_tile(tile, page_url) for tile in tiles)
                    if row["part_number"]]
    return []


def _from_tile(tile, page_url=""):
    number = _first(tile, "number")
    if not number:
        match = NUMBER_LABELS.search(" ".join(tile.css("::text").getall()))
        number = match.group(1).strip() if match else ""
    href = _first(tile, "url")
    return {
        "part_number": number,
        "manufacturer": _first(tile, "brand"),
        "title": " ".join(_first(tile, "title").split()),
        "price": _number(_first(tile, "price")),
        "currency": "",
        "url": _absolute(href, page_url),
        "image_url": _first(tile, "image"),
        "oe_numbers": [],
        "source": "html",
    }


# ---------------------------------------------------------------------------
# עמוד מוצר: המספרים המקוריים
# ---------------------------------------------------------------------------

def parse_oe_numbers(html):
    """המק"טים המקוריים שרשומים בעמוד המוצר, בלי כפילויות."""
    selector = Selector(text=html or "")
    found = []

    for node in _ld_blocks(selector):
        if _is_product(node):
            for value in (node.get("mpn"), node.get("gtin13")):
                if value:
                    found.append(str(value).strip())

    for query in selectors("oe"):
        found.extend(value.strip() for value in selector.css(query).getall())

    if not found:
        text = " ".join(selector.css("::text").getall())
        match = OE_LABELS.search(text)
        if match:
            found.extend(OE_NUMBER.findall(match.group(1)))

    unique = []
    for value in found:
        cleaned = value.strip(" ,;·")
        if cleaned and cleaned.lower() not in {u.lower() for u in unique}:
            unique.append(cleaned)
    return unique[:10]
