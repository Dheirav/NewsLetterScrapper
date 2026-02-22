"""
services/newsletter/repository.py
------------------------------------
Persist and retrieve Newsletter records.

Usage:
    from services.newsletter.repository import save_newsletter, get_newsletter_for_date
"""
import logging
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.orm_models import NewsletterORM
from core.schemas.models import Newsletter

log = logging.getLogger(__name__)


async def save_newsletter(newsletter: Newsletter, session: AsyncSession) -> NewsletterORM:
    """Insert or replace the newsletter for a given date."""
    if not newsletter.html_content:
        raise ValueError(
            "Cannot save newsletter with empty html_content — call render_html first"
        )
    # Check for an existing record on this date
    result = await session.execute(
        select(NewsletterORM).where(NewsletterORM.newsletter_date == newsletter.date)
    )
    orm = result.scalar_one_or_none()

    if orm is None:
        orm = NewsletterORM(newsletter_date=newsletter.date)
        session.add(orm)

    orm.html_content = newsletter.html_content
    await session.flush()
    newsletter.id = orm.id
    log.info("Newsletter saved for %s (id=%s)", newsletter.date, orm.id)
    return orm


async def mark_sent(newsletter_id: int, session: AsyncSession) -> None:
    """Mark a newsletter as sent after successful delivery."""
    orm = await session.get(NewsletterORM, newsletter_id)
    if orm:
        orm.sent = True
        orm.sent_at = datetime.now(tz=timezone.utc)
        await session.flush()


async def get_newsletter_for_date(
    target_date: date, session: AsyncSession
) -> NewsletterORM | None:
    result = await session.execute(
        select(NewsletterORM).where(NewsletterORM.newsletter_date == target_date)
    )
    return result.scalar_one_or_none()
