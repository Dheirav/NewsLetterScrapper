"""
services/knowledge/repository.py
-----------------------------------
Persist KnowledgeStory objects to the database.

Usage:
    from services.knowledge.repository import save_knowledge_story
    await save_knowledge_story(story, session)
"""
import logging
from datetime import date
from typing import List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.orm_models import KnowledgeStoryORM
from core.schemas.models import KnowledgeStory

log = logging.getLogger(__name__)


async def save_knowledge_story(
    story: KnowledgeStory,
    session: AsyncSession,
) -> KnowledgeStoryORM:
    """Insert or update a KnowledgeStory (upsert by cluster_id).
    
    Safe to call on re-runs: if a row for this cluster already exists it will
    be updated rather than raising an IntegrityError.
    """
    result = await session.execute(
        select(KnowledgeStoryORM).where(KnowledgeStoryORM.cluster_id == story.cluster_id)
    )
    orm = result.scalar_one_or_none()
    if orm is None:
        orm = KnowledgeStoryORM(cluster_id=story.cluster_id, story_date=date.today())
        session.add(orm)
    orm.topic_label = story.topic_label
    orm.executive_summary = story.executive_summary
    orm.context = story.context
    orm.why_it_matters = story.why_it_matters
    orm.implications = story.implications
    orm.talking_points = story.talking_points
    orm.reliability_notes = story.reliability_notes
    orm.source_count = story.source_count
    orm.article_urls = story.article_urls
    orm.article_sources = story.article_sources
    await session.flush()
    return orm


async def get_stories_for_date(
    target_date: date,
    session: AsyncSession,
) -> List[KnowledgeStory]:
    """Load all KnowledgeStory objects for a given date from the DB."""
    result = await session.execute(
        select(KnowledgeStoryORM).where(KnowledgeStoryORM.story_date == target_date)
    )
    rows = result.scalars().all()

    stories = []
    for row in rows:
        stories.append(
            KnowledgeStory(
                cluster_id=row.cluster_id,
                topic_label=row.topic_label,
                executive_summary=row.executive_summary,
                context=row.context,
                why_it_matters=row.why_it_matters,
                implications=row.implications,
                talking_points=row.talking_points or [],
                reliability_notes=row.reliability_notes,
                source_count=row.source_count,
                article_urls=row.article_urls or [],
                article_sources=row.article_sources or [],
            )
        )
    return stories
