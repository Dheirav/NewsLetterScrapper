"""
services/newsletter/emailer.py
--------------------------------
Send the rendered HTML newsletter via SMTP using aiosmtplib.

Usage:
    from services.newsletter.emailer import send_email
    await send_email(html_content, subject="Intelligence Briefing — Feb 21 2026")
"""
import logging
import re
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib

from core.config import settings

log = logging.getLogger(__name__)


async def send_email(
    html_content: str,
    subject: str,
    recipient: str | None = None,
) -> bool:
    """
    Send an HTML email via the configured SMTP server.
    Returns True on success, False on failure.
    """
    to_address = recipient or settings.recipient_email
    if not to_address:
        log.warning("No recipient email configured — skipping send")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{settings.email_from_name} <{settings.smtp_user}>"
    msg["To"] = to_address

    # Attach plain-text fallback (strip tags crudely)
    plain = re.sub(r"<[^>]+>", " ", html_content)
    plain = re.sub(r"\s+", " ", plain).strip()
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html_content, "html", "utf-8"))

    try:
        await aiosmtplib.send(
            msg,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_user,
            password=settings.smtp_password,
            start_tls=True,
        )
        log.info("Newsletter sent to %s", to_address)
        return True
    except Exception as exc:
        log.error("Failed to send newsletter email: %s", exc)
        return False
