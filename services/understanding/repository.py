"""
services/understanding/repository.py
--------------------------------------
Persist story clusters (and update article cluster_id assignments) to the DB.

Usage:
    from services.understanding.repository import save_clusters
    await save_clusters(clusters, session)
"""
import logging
from datetime import date, datetime, timezone
from typing import List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.orm_models import ArticleORM, StoryClusterORM
from core.schemas.models import StoryCluster

log = logging.getLogger(__name__)


async def save_clusters(
    clusters: List[StoryCluster], session: AsyncSession
) -> None:
    """
    For each cluster:
      1. Insert a StoryClusterORM row
      2. Update the cluster_id on each ArticleORM row
    """
    if not clusters:
        return

    today = date.today()

    for cluster in clusters:
        existing = await session.execute(
            select(StoryClusterORM).where(StoryClusterORM.cluster_id == cluster.cluster_id)
        )
        orm_cluster = existing.scalar_one_or_none()
        if orm_cluster is None:
            orm_cluster = StoryClusterORM(
                cluster_id=cluster.cluster_id,
                cluster_date=today,
                created_at=datetime.now(tz=timezone.utc),
            )
            session.add(orm_cluster)
        orm_cluster.topic_label = cluster.topic_label
        orm_cluster.article_count = len(cluster.articles)

        # Update article rows
        article_ids = [a.id for a in cluster.articles if a.id is not None]
        if article_ids:
            result = await session.execute(
                select(ArticleORM).where(ArticleORM.id.in_(article_ids))
            )
            for row in result.scalars():
                row.cluster_id = cluster.cluster_id

    await session.flush()
    log.info("Saved %d story clusters to the database", len(clusters))
