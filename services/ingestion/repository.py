"""
services/ingestion/repository.py
----------------------------------
Persist ingested articles to the database.

Usage:
    from services.ingestion.repository import save_articles
    saved = await save_articles(articles, session)
"""
import logging
from typing import List

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.orm_models import ArticleORM
from core.schemas.models import Article

log = logging.getLogger(__name__)


async def save_articles(
    articles: List[Article], session: AsyncSession
) -> List[Article]:
    """
    Upsert articles into the DB using INSERT … ON CONFLICT (url) DO NOTHING.
    Duplicate URLs are silently skipped (idempotent on re-runs).
    After flushing, fetches the DB-assigned id for every article by URL.
    Returns the same list with .id fields populated (duplicates get their
    existing id so downstream steps can still use them).
    """
    if not articles:
        return articles

    rows = [
        {
            "title": a.title,
            "source": a.source,
            "url": a.url,
            "published_at": a.published_at,
            "content": a.content,
            "content_quality": a.content_quality,
            "source_type": a.source_type,
            "source_weight": a.source_weight,
        }
        for a in articles
    ]

    stmt = insert(ArticleORM).values(rows).on_conflict_do_nothing(index_elements=["url"])
    await session.execute(stmt)
    await session.flush()

    # Fetch back the id for every URL (whether just inserted or pre-existing)
    urls = [a.url for a in articles]
    result = await session.execute(
        select(ArticleORM.url, ArticleORM.id).where(ArticleORM.url.in_(urls))
    )
    url_to_id = {row.url: row.id for row in result}

    inserted = 0
    skipped = 0
    for article in articles:
        article.id = url_to_id.get(article.url)
        if article.id:
            inserted += 1
        else:
            skipped += 1

    log.info("Articles saved: %d new, %d already existed", inserted, skipped)
    return articles
