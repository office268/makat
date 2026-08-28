"""זיהוי סוג החלק - מטקסט חופשי או מתמונה.

שתי דרכים, ושתיהן מחזירות את אותו מבנה תוצאה:
  * `identify_from_text`  - התאמת מילים נרדפות בעברית. עובד תמיד, בלי רשת ובלי מפתח.
  * `identify_from_image` - סיווג ויזואלי באמצעות Claude. דורש מפתח API.

חשוב: שתיהן מחזירות *סוג חלק*, לא מק"ט. המק"ט נסגר רק בהצטלבות עם הרכב.
"""
import base64
import os
import re

from .taxonomy import PART_TYPES, search_terms, type_name

VISION_MODEL = os.environ.get("VISION_MODEL", "claude-opus-5")

_PUNCT = re.compile(r"[^\w֐-׿]+")


def _normalize(text):
    """מוריד ניקוד, סימני פיסוק ואותיות סופיות כדי שההשוואה תהיה סלחנית."""
    text = re.sub(r"[֑-ׇ]", "", (text or "").strip().lower())
    text = _PUNCT.sub(" ", text)
    finals = str.maketrans("ךםןףץ", "כמנפצ")
    return " ".join(text.translate(finals).split())


def _result(key, confidence, method, note=None):
    return {
        "part_type": key,
        "name": type_name(key) if key else None,
        "category": PART_TYPES[key][2] if key else None,
        "confidence": round(confidence, 2),
        "method": method,
        "note": note,
    }


def identify_from_text(text, top_n=3):
    """מדרג סוגי חלקים מול טקסט חופשי בעברית. מחזיר רשימה ממויינת."""
    query = _normalize(text)
    if not query:
        return []
    query_words = set(query.split())

    scored = []
    for key in PART_TYPES:
        best = 0.0
        for term in search_terms(key):
            term_norm = _normalize(term)
            if not term_norm:
                continue
            if term_norm == query:
                best = max(best, 1.0)
            elif term_norm in query or query in term_norm:
                # ככל שהמונח מכסה יותר מהשאילתה, הביטחון גבוה יותר
                ratio = min(len(term_norm), len(query)) / max(len(term_norm), len(query))
                best = max(best, 0.6 + 0.3 * ratio)
            else:
                overlap = query_words & set(term_norm.split())
                if overlap:
                    best = max(best, 0.35 + 0.25 * len(overlap) / len(query_words))
        if best > 0:
            scored.append(_result(key, best, "text"))

    scored.sort(key=lambda r: r["confidence"], reverse=True)
    return scored[:top_n]


def vision_available():
    """האם קיימים אישורי גישה ל-Claude ו-SDK מותקן."""
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return bool(
        os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    )


def _catalog_prompt():
    lines = [f"{key}: {he} ({en})" for key, (he, en, _c, _s) in PART_TYPES.items()]
    return "\n".join(lines)


def identify_from_image(image_bytes, media_type="image/jpeg", hint=None):
    """מסווג סוג חלק מתמונה באמצעות Claude. מחזיר רשימה עם תוצאה אחת, או [] אם אין גישה."""
    if not vision_available():
        return []

    import anthropic

    system = (
        "אתה מסווג חלקי חילוף לרכב. תקבל תמונה של חלק, ותחזיר את סוג החלק "
        "מתוך הרשימה הסגורה הבאה בלבד (המפתח באנגלית משמאל לנקודתיים):\n\n"
        f"{_catalog_prompt()}\n\n"
        "החזר שורה אחת בלבד בפורמט: key|confidence\n"
        "כאשר key הוא המפתח המדויק מהרשימה, ו-confidence הוא מספר בין 0 ל-1.\n"
        "אם התמונה אינה של חלק רכב מהרשימה, החזר: unknown|0\n"
        "אל תוסיף הסברים, סימני פיסוק או טקסט נוסף."
    )
    content = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": base64.standard_b64encode(image_bytes).decode("utf-8"),
            },
        },
        {"type": "text", "text": hint or "איזה סוג חלק זה?"},
    ]

    client = anthropic.Anthropic()
    try:
        response = client.messages.create(
            model=VISION_MODEL,
            max_tokens=256,
            system=system,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": content}],
        )
    except Exception as exc:  # שגיאת רשת/מכסה לא אמורה להפיל את הדמו
        return [_result(None, 0.0, "vision", f"שגיאה בקריאה ל-Claude: {exc}")]

    if response.stop_reason == "refusal":
        return [_result(None, 0.0, "vision", "הבקשה נדחתה על ידי המודל")]

    text = "".join(b.text for b in response.content if b.type == "text").strip()
    return [_parse_vision_reply(text)]


def _parse_vision_reply(text):
    key, _, raw_conf = text.partition("|")
    key = key.strip()
    try:
        confidence = float(raw_conf.strip())
    except ValueError:
        confidence = 0.5
    if key not in PART_TYPES:
        return _result(None, 0.0, "vision", f"לא זוהה סוג חלק מוכר (התקבל: {text[:60]})")
    return _result(key, confidence, "vision")


def identify(text=None, image_bytes=None, media_type="image/jpeg"):
    """נקודת הכניסה: מנסה תמונה קודם, ונופל לטקסט."""
    if image_bytes:
        results = identify_from_image(image_bytes, media_type, hint=text)
        if results and results[0]["part_type"]:
            return results
        # אם הזיהוי הוויזואלי לא הצליח אבל יש טקסט - ננסה אותו
        if text:
            fallback = identify_from_text(text)
            if fallback:
                return fallback
        return results
    return identify_from_text(text)
