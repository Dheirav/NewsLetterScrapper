"""
scripts/archive.py
-------------------
Delete articles (and their orphaned clusters/stories) older than N days.

Keeps the database lean — after 90 days you will otherwise accumulate tens of
thousands of articles with 768-dim embeddings consuming gigabytes in Postgres.

Usage:
    python scripts/archive.py                  # uses ARCHIVE_KEEP_DAYS from config
    python scripts/archive.py --days 60        # override retention window
    python scripts/archive.py --dry-run        # show what would be deleted

The cleanup order respects FK constraints:
  1. knowledge_stories  (FK → story_clusters)
  2. articles           (FK → story_clusters)
  3. story_clusters
  4. pipeline_runs
  5. failed_generations

reading_events and user_profiles are NOT touched — they are small and important
for personalization continuity.
"""
import argparse
import asyncio
import logging
from datetime import date, timedelta

from sqlalchemy import delete, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.db.orm_models import (
    ArticleORM,
    FailedGenerationORM,
    KnowledgeStoryORM,
    PipelineRunORM,
    StoryClusterORM,
)
from core.db.session import get_session

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("archive")


async def _count(session: AsyncSession, orm_class, cutoff: date) -> int:
    """Count rows older than cutoff in a table that has a date column."""
    date_col = getattr(orm_class, "cluster_date", None) or getattr(
        orm_class, "run_date", None
    )
    if date_col is None:
        return 0
    result = await session.execute(
        select(func.count()).select_from(orm_class).where(date_col < cutoff)
    )
    return result.scalar() or 0


async def archive(keep_days: int, dry_run: bool = False) -> None:
    cutoff = date.today() - timedelta(days=keep_days)
    log.info(
        "Archive: deleting data older than %s (%d-day window)%s",
        cutoff,
        keep_days,
        " [DRY RUN]" if dry_run else "",
    )

    async with get_session() as session:
        # ── Count candidates ───────────────────────────────────────────────
        old_clusters_result = await session.execute(
            select(StoryClusterORM.cluster_id).where(
                StoryClusterORM.cluster_date < cutoff
            )
        )
        old_cluster_ids = [row[0] for row in old_clusters_result.fetchall()]

        stories_count = await session.execute(
            select(func.count(KnowledgeStoryORM.id)).where(
                KnowledgeStoryORM.cluster_id.in_(old_cluster_ids)
            )
            if old_cluster_ids else
            select(func.count(KnowledgeStoryORM.id)).where(False)
        )
        articles_count = await session.execute(
            select(func.count(ArticleORM.id)).where(
                ArticleORM.cluster_id.in_(old_cluster_ids)
            )
            if old_cluster_ids else
            select(func.count(ArticleORM.id)).where(False)
        )
        runs_count = await session.execute(
            select(func.count(PipelineRunORM.id)).where(
                PipelineRunORM.run_date < cutoff
            )
        )
        failed_count = await session.execute(
            select(func.count(FailedGenerationORM.id)).where(
                FailedGenerationORM.run_date < cutoff
            )
        )

        log.info(
            "Would delete: %d clusters, %d stories, %d articles, "
            "%d pipeline_runs, %d failed_generations",
            len(old_cluster_ids),
            stories_count.scalar() or 0,
            articles_count.scalar() or 0,
            runs_count.scalar() or 0,
            failed_count.scalar() or 0,
        )

        if dry_run:
            log.info("Dry run — no data deleted.")
            return

        if not old_cluster_ids:
            log.info("Nothing to archive.")
            return

        # ── Delete in FK-safe order ────────────────────────────────────────
        # 1. Knowledge stories
        r = await session.execute(
            delete(KnowledgeStoryORM).where(
                KnowledgeStoryORM.cluster_id.in_(old_cluster_ids)
            )
        )
        log.info("Deleted %d knowledge_stories", r.rowcount)

        # 2. Articles
        r = await session.execute(
            delete(ArticleORM).where(ArticleORM.cluster_id.in_(old_cluster_ids))
        )
        log.info("Deleted %d articles", r.rowcount)

        # 3. Clusters
        r = await session.execute(
            delete(StoryClusterORM).where(
                StoryClusterORM.cluster_id.in_(old_cluster_ids)
            )
        )
        log.info("Deleted %d story_clusters", r.rowcount)

        # 4. Pipeline run logs
        r = await session.execute(
            delete(PipelineRunORM).where(PipelineRunORM.run_date < cutoff)
        )
        log.info("Deleted %d pipeline_runs", r.rowcount)

        # 5. Failed generation records
        r = await session.execute(
            delete(FailedGenerationORM).where(
                FailedGenerationORM.run_date < cutoff
            )
        )
        log.info("Deleted %d failed_generations", r.rowcount)

        await session.commit()
        log.info("Archive complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Archive articles and clusters older than N days"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=settings.archive_keep_days,
        help=f"Retention window in days (default: {settings.archive_keep_days} from ARCHIVE_KEEP_DAYS env var)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without actually deleting anything",
    )
    args = parser.parse_args()
    asyncio.run(archive(keep_days=args.days, dry_run=args.dry_run))
