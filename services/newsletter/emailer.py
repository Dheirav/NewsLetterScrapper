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


def _build_message(html_content: str, subject: str, to_address: str) -> MIMEMultipart:
    """Build a MIME message addressed to a single recipient."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{settings.email_from_name} <{settings.smtp_user}>"
    msg["To"] = to_address

    plain = re.sub(r"<[^>]+>", " ", html_content)
    plain = re.sub(r"\s+", " ", plain).strip()
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html_content, "html", "utf-8"))
    return msg


async def send_email(
    html_content: str,
    subject: str,
    recipients: list[str] | str | None = None,
) -> bool:
    """
    Send an HTML email via the configured SMTP server.
    Each recipient gets their own individual email so no one can see
    the other recipients.
    `recipients` can be a single address, a list of addresses, or None
    (falls back to all_recipients from settings).
    Returns True if the message was delivered to at least one recipient.
    """
    # Normalise to a list
    if recipients is None:
        to_list = settings.all_recipients
    elif isinstance(recipients, str):
        to_list = [recipients]
    else:
        to_list = list(recipients)

    to_list = [a.strip() for a in to_list if a.strip()]
    if not to_list:
        log.warning("No recipient email configured — skipping send")
        return False

    smtp_kwargs = dict(
        hostname=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_user,
        password=settings.smtp_password,
        start_tls=True,
    )

    succeeded, failed = 0, 0
    for address in to_list:
        msg = _build_message(html_content, subject, address)
        try:
            await aiosmtplib.send(msg, **smtp_kwargs)
            log.info("Newsletter sent to %s", address)
            succeeded += 1
        except Exception as exc:
            log.error("Failed to send to %s: %s", address, exc)
            failed += 1

    if succeeded:
        log.info("Delivery complete — %d sent, %d failed", succeeded, failed)
    return succeeded > 0
