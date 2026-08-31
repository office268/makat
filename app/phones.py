"""מספר טלפון כזהות: נרמול והצגה.

אותו מספר נכתב בעשר צורות - עם מקף, עם רווח, עם קידומת בין־לאומית,
מתוך אנשי הקשר של הטלפון. כדי שהשוואה מול המורשים תהיה השוואה של
מספרים ולא של מחרוזות, כל מספר עובר לצורה מקומית אחת: 0501234567.
"""
import re

_NOT_DIGITS = re.compile(r"\D+")


def normalize(raw):
    """מחזיר את המספר בצורתו המקומית, או None אם אינו מספר ישראלי תקין.

    מקבל 052-797-7040, ‎+972 52 797 7040 ו-00972527977040 כאותו מספר.
    האורך הוא מה שמבדיל בין נייד (עשר ספרות) לקווי (תשע).
    """
    digits = _NOT_DIGITS.sub("", raw or "")
    if digits.startswith("00972"):
        digits = "0" + digits[5:]
    elif digits.startswith("972"):
        digits = "0" + digits[3:]
    if not digits.startswith("0") or len(digits) not in (9, 10):
        return None
    return digits


def display(phone):
    """המספר כפי שמסתכלים עליו: 0532798782 -> 053-279-8782."""
    if not phone:
        return "—"
    if len(phone) == 10:
        return f"{phone[:3]}-{phone[3:6]}-{phone[6:]}"
    if len(phone) == 9:
        return f"{phone[:2]}-{phone[2:5]}-{phone[5:]}"
    return phone
