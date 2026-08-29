"""שליחת דוא"ל.

מוגדר דרך משתני סביבה. כשאין הגדרה, ההודעות נכתבות ללוג במקום להישלח -
כך שהמערכת עובדת גם בפיתוח ובסביבה שעוד לא חוברה לספק דוא"ל, ובלי
להעמיד פנים שההודעה יצאה.

  MAIL_SERVER, MAIL_PORT, MAIL_USERNAME, MAIL_PASSWORD, MAIL_SENDER
  MAIL_USE_TLS=1
"""
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

from flask import current_app


def is_configured():
    return bool(os.environ.get("MAIL_SERVER") and os.environ.get("MAIL_SENDER"))


def _build(to, subject, body):
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr(
        (os.environ.get("MAIL_SENDER_NAME", 'קטלוג מק"טים'), os.environ["MAIL_SENDER"])
    )
    message["To"] = to
    message.set_content(body)
    return message


def send(to, subject, body):
    """שולח הודעה. מחזיר True אם יצאה בפועל, False אם רק נרשמה ללוג."""
    if not is_configured():
        current_app.logger.info(
            "דוא\"ל לא מוגדר - ההודעה לא נשלחה.\n  אל: %s\n  נושא: %s\n%s",
            to, subject, body,
        )
        return False

    try:
        message = _build(to, subject, body)
        host = os.environ["MAIL_SERVER"]
        port = int(os.environ.get("MAIL_PORT", 587))
        use_tls = os.environ.get("MAIL_USE_TLS", "1").strip() == "1"

        with smtplib.SMTP(host, port, timeout=15) as server:
            if use_tls:
                server.starttls(context=ssl.create_default_context())
            username = os.environ.get("MAIL_USERNAME")
            if username:
                server.login(username, os.environ.get("MAIL_PASSWORD", ""))
            server.send_message(message)
        current_app.logger.info('נשלח דוא"ל אל %s: %s', to, subject)
        return True
    except Exception as exc:
        # כשל בשליחה לא אמור להפיל את הפעולה שיצרה אותה
        current_app.logger.error('שליחת דוא"ל אל %s נכשלה: %s', to, exc)
        return False


def send_invitation(invitation, accept_url, inviter):
    """הזמנת עובד לארגון."""
    organization = invitation.organization.name
    body = f"""שלום,

{inviter.full_name or inviter.email} מזמין אותך להצטרף ל{organization}
במערכת קטלוג המק"טים, בתפקיד {invitation.role_label}.

להצטרפות, בחירת סיסמה וכניסה למערכת:
{accept_url}

הקישור תקף עד {invitation.expires_at.strftime('%d/%m/%Y')}.

אם לא ציפית להזמנה הזו, אפשר פשוט להתעלם ממנה.
"""
    return send(invitation.email, f"הזמנה להצטרף ל{organization}", body)
