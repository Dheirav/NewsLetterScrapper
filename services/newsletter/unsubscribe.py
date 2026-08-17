"""
services/newsletter/unsubscribe.py
------------------------------------
Manage email opt-outs (unsubscribes).

Functions
---------
make_token(email)           — generate HMAC-SHA256 token for unsubscribe link
verify_token(email, token)  — constant-time token verification
make_mailto_link(email)     — hosting-free unsubscribe target for List-Unsubscribe
opt_out(email, session)     — add address to the unsubscribes table
is_opted_out(email, session)— check if an address has opted out
get_all_opted_out(session)  — return every opted-out address
"""
import hashlib
import hmac
import logging
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.db.orm_models import UnsubscribeORM

log = logging.getLogger(__name__)


def make_token(email: str) -> str:
    """Generate a signed HMAC-SHA256 token for a one-click unsubscribe link."""
    secret = settings.unsubscribe_secret or settings.smtp_password or "changeme"
    return hmac.new(
        secret.encode(),
        email.lower().strip().encode(),
        digestmod=hashlib.sha256,
    ).hexdigest()


def verify_token(email: str, token: str) -> bool:
    """Constant-time comparison to prevent timing attacks."""
    expected = make_token(email)
    return hmac.compare_digest(expected, token)


def make_mailto_link(email: str) -> str:
    """
    Build a ``mailto:`` unsubscribe target that needs no hosting at all.

    This is the fallback that keeps opt-out working when PUBLIC_URL is not
    reachable — a self-hosted pipeline on a laptop is asleep most of the time,
    but a recipient can still unsubscribe at 2am by sending mail. The signed
    token travels in the body so the request can be verified (and later
    processed automatically) exactly like the HTTPS route.
    """
    subject = quote(f"Unsubscribe {email}")
    body = quote(
        "Send this message as-is to be removed from the Intelligence Briefing.\n\n"
        f"address: {email}\n"
        f"token: {make_token(email)}\n"
    )
    return f"mailto:{settings.smtp_user}?subject={subject}&body={body}"


async def opt_out(email: str, session: AsyncSession) -> bool:
    """Mark *email* as opted out.

    Returns True if a new record was inserted, False if the address was
    already in the opt-out list.
    """
    address = email.lower().strip()
    result = await session.execute(
        select(UnsubscribeORM).where(UnsubscribeORM.email == address)
    )
    if result.scalar_one_or_none() is not None:
        log.debug("Unsubscribe: %s already opted out", address)
        return False
    session.add(UnsubscribeORM(email=address))
    await session.flush()
    log.info("Unsubscribed: %s", address)
    return True


async def is_opted_out(email: str, session: AsyncSession) -> bool:
    """Return True if *email* is in the opt-out list."""
    address = email.lower().strip()
    result = await session.execute(
        select(UnsubscribeORM).where(UnsubscribeORM.email == address)
    )
    return result.scalar_one_or_none() is not None


async def get_all_opted_out(session: AsyncSession) -> list[str]:
    """Return all opted-out email addresses sorted alphabetically."""
    result = await session.execute(
        select(UnsubscribeORM).order_by(UnsubscribeORM.email)
    )
    return [row.email for row in result.scalars().all()]
