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
from urllib.parse import quote

import aiosmtplib

from core.config import settings
from core.db.session import get_session

log = logging.getLogger(__name__)


def _build_message(html_content: str, subject: str, to_address: str) -> MIMEMultipart:
    """Build a MIME message addressed to a single recipient.

    Sets a per-recipient ``List-Unsubscribe`` header and replaces the
    ``__UNSUBSCRIBE_LINK__`` placeholder in the body.

    Two unsubscribe routes are offered, and which ones are available depends on
    whether PUBLIC_URL is actually reachable:

    * ``mailto:`` — always present. Needs no hosting, so it keeps working while
      the machine running this pipeline is asleep. This is the only route that
      functions for a laptop-hosted deployment.
    * ``https://`` — added only when PUBLIC_URL is reachable, and advertised as
      RFC 8058 one-click only when it is also HTTPS. Claiming one-click without
      a POST-capable HTTPS endpoint gives mail clients a button that fails.
    """
    # Lazy import to avoid a circular dependency at module load time.
    from services.newsletter.unsubscribe import (  # noqa: PLC0415
        make_mailto_link,
        make_token,
    )

    mailto_link = make_mailto_link(to_address)
    targets = []
    https_url = None

    if settings.public_url_is_reachable:
        https_url = (
            f"{settings.public_url}/api/newsletter/unsubscribe"
            f"?email={quote(to_address)}&token={make_token(to_address)}"
        )
        targets.append(f"<{https_url}>")
    targets.append(f"<{mailto_link}>")

    # The visible footer link falls back to mailto when there is no reachable
    # host, so recipients never see a localhost URL.
    html_content = html_content.replace(
        "__UNSUBSCRIBE_LINK__", https_url or mailto_link
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{settings.email_from_name} <{settings.smtp_user}>"
    msg["To"] = to_address
    msg["List-Unsubscribe"] = ", ".join(targets)
    if https_url and settings.supports_one_click_unsubscribe:
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

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
    Opted-out addresses are automatically skipped.
    `recipients` can be a single address, a list of addresses, or None
    (falls back to all_recipients from settings).
    Returns True if the message was delivered to at least one recipient.
    """
    # Lazy import to avoid a circular dependency at module load time.
    from services.newsletter.unsubscribe import is_opted_out  # noqa: PLC0415

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

    # Filter out opted-out addresses.
    async with get_session() as session:
        filtered: list[str] = []
        for addr in to_list:
            if await is_opted_out(addr, session):
                log.info("Skipping opted-out recipient: %s", addr)
            else:
                filtered.append(addr)
    to_list = filtered
    if not to_list:
        log.warning("All recipients have opted out — skipping send")
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
