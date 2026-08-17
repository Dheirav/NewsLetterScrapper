"""
scripts/archive.py
-------------------
Reclaim disk by ageing out old pipeline data.

Retention is TIERED, because articles and stories cost very different amounts
to keep:

  articles (ARCHIVE_KEEP_ARTICLES_DAYS, default 90)
      Raw scraped text plus a 768-dim embedding each. This is effectively all
      of the database's growth. Deleted once past the window — including
      articles that never joined a cluster.

  stories + clusters (ARCHIVE_KEEP_STORIES_DAYS, default 365)
      The distilled briefings the pipeline exists to produce, a few KB each.
      Kept far longer. Their articles may already be gone; that is intended —
      the story text stands on its own and retains the source URLs.

  newsletters
      Rows are never deleted (the metadata is tiny), but the rendered HTML
      (~150 KB/day) is nulled out past the article window. A newsletter can be
      re-rendered from its stories with `send_newsletter.py --rerender`.

Usage:
    python scripts/archive.py --dry-run       # show what would go (start here)
    python scripts/archive.py                 # apply, using .env windows
    python scripts/archive.py --article-days 60 --story-days 730
    python scripts/archive.py --no-vacuum     # skip space reclamation

Safety: a run that would remove most of the article table stops and asks for
--force. That is not hypothetical — if the pipeline has not run for a while,
every row falls outside the window and a default run would empty the database.
"""
import argparse
import asyncio
import logging
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.db.orm_models import (
    ArticleORM,
    FailedGenerationORM,
    KnowledgeStoryORM,
    NewsletterORM,
    PipelineRunORM,
    StoryClusterORM,
)
from core.db.session import get_engine, get_session

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("archive")

# Refuse to delete a larger share of the article table than this without --force.
_RUNAWAY_DELETE_RATIO = 0.5

# Tables worth reclaiming space on; the rest are small.
_VACUUM_TABLES = ("articles", "story_clusters", "knowledge_stories", "newsletters")


def _as_utc_datetime(day: date) -> datetime:
    """articles.created_at is timestamptz; cutoffs are dates."""
    return datetime.combine(day, time.min, tzinfo=timezone.utc)


async def _scalar(session: AsyncSession, stmt) -> int:
    return (await session.execute(stmt)).scalar() or 0


async def _survey(
    session: AsyncSession, article_cutoff: date, story_cutoff: date
) -> dict[str, int]:
    """Count everything the run would touch, without touching it."""
    article_ts = _as_utc_datetime(article_cutoff)

    return {
        "articles_total": await _scalar(session, select(func.count(ArticleORM.id))),
        "articles_expired": await _scalar(
            session,
            select(func.count(ArticleORM.id)).where(ArticleORM.created_at < article_ts),
        ),
        # Called out separately because the previous implementation deleted only
        # articles belonging to an expired cluster, so these accumulated forever:
        # paywall failures and singletons that never clustered.
        "articles_unclustered": await _scalar(
            session,
            select(func.count(ArticleORM.id)).where(
                ArticleORM.created_at < article_ts,
                ArticleORM.cluster_id.is_(None),
            ),
        ),
        "stories_expired": await _scalar(
            session,
            select(func.count(KnowledgeStoryORM.id)).where(
                KnowledgeStoryORM.story_date < story_cutoff
            ),
        ),
        "clusters_expired": await _scalar(
            session,
            select(func.count(StoryClusterORM.cluster_id)).where(
                StoryClusterORM.cluster_date < story_cutoff
            ),
        ),
        "newsletters_to_strip": await _scalar(
            session,
            select(func.count(NewsletterORM.id)).where(
                NewsletterORM.newsletter_date < article_cutoff,
                NewsletterORM.html_content != "",
            ),
        ),
        "runs_expired": await _scalar(
            session,
            select(func.count(PipelineRunORM.id)).where(
                PipelineRunORM.run_date < article_cutoff
            ),
        ),
        "failed_expired": await _scalar(
            session,
            select(func.count(FailedGenerationORM.id)).where(
                FailedGenerationORM.run_date < article_cutoff
            ),
        ),
    }


def _report(counts: dict[str, int], article_cutoff: date, story_cutoff: date) -> None:
    log.info("Articles  older than %s  ->  delete %d of %d (%d never clustered)",
             article_cutoff, counts["articles_expired"], counts["articles_total"],
             counts["articles_unclustered"])
    log.info("Stories   older than %s  ->  delete %d", story_cutoff, counts["stories_expired"])
    log.info("Clusters  older than %s  ->  delete %d", story_cutoff, counts["clusters_expired"])
    log.info("Newsletter HTML older than %s  ->  strip %d (rows kept)",
             article_cutoff, counts["newsletters_to_strip"])
    log.info("Pipeline runs / failures  ->  delete %d / %d",
             counts["runs_expired"], counts["failed_expired"])


async def _vacuum() -> None:
    """
    Postgres marks deleted rows dead but does not return the space until
    VACUUM runs. It cannot execute inside a transaction, hence AUTOCOMMIT.
    """
    engine = get_engine().execution_options(isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        for table in _VACUUM_TABLES:
            log.info("VACUUM ANALYZE %s …", table)
            await conn.execute(text(f"VACUUM ANALYZE {table}"))
    log.info("Space reclaimed.")


async def archive(
    article_days: int,
    story_days: int,
    dry_run: bool = False,
    force: bool = False,
    vacuum: bool = True,
    session_factory=None,
) -> None:
    """
    ``session_factory`` is injectable so this can be pointed at a test database.
    Reaching for the global ``get_session`` unconditionally made the destructive
    path impossible to exercise safely: a test could set up fixtures in an
    isolated schema and this function would still open its own connection to
    whatever DATABASE_URL names, and delete from there.

    It defaults to None and is resolved on each call rather than defaulting to
    ``get_session`` directly. A default argument is evaluated once at import,
    binding the original function object — so a test that patches
    ``archive.get_session`` would be silently ignored and the real database
    deleted from anyway. That is not a hypothetical either.
    """
    if session_factory is None:
        session_factory = get_session
    today = date.today()
    article_cutoff = today - timedelta(days=article_days)
    story_cutoff = today - timedelta(days=story_days)

    if story_cutoff > article_cutoff:
        log.warning(
            "Story retention (%dd) is shorter than article retention (%dd) — "
            "stories will be discarded while their articles remain.",
            story_days, article_days,
        )

    log.info("Archive%s — articles %dd, stories %dd",
             " [DRY RUN]" if dry_run else "", article_days, story_days)

    async with session_factory() as session:
        counts = await _survey(session, article_cutoff, story_cutoff)
        _report(counts, article_cutoff, story_cutoff)

        if dry_run:
            log.info("Dry run — nothing deleted.")
            return

        total, expired = counts["articles_total"], counts["articles_expired"]
        if not force and total and expired / total > _RUNAWAY_DELETE_RATIO:
            log.error(
                "Refusing to delete %d of %d articles (%.0f%% of the table). "
                "This usually means the pipeline has not run recently, so every "
                "row has aged out. Widen the window with --article-days, or pass "
                "--force if this is genuinely what you want.",
                expired, total, 100 * expired / total,
            )
            raise SystemExit(1)

        if not any(v for k, v in counts.items() if k != "articles_total"):
            log.info("Nothing to archive.")
            return

        article_ts = _as_utc_datetime(article_cutoff)

        # Articles first: they hold the FK to story_clusters. Filtering on
        # created_at (not cluster membership) is what finally reaches the
        # never-clustered rows.
        result = await session.execute(
            delete(ArticleORM).where(ArticleORM.created_at < article_ts)
        )
        log.info("Deleted %d articles", result.rowcount)

        result = await session.execute(
            delete(KnowledgeStoryORM).where(KnowledgeStoryORM.story_date < story_cutoff)
        )
        log.info("Deleted %d knowledge_stories", result.rowcount)

        # Safe now: any article still referencing these was removed above, and
        # older articles pointing at newer clusters cannot exist.
        result = await session.execute(
            delete(StoryClusterORM).where(StoryClusterORM.cluster_date < story_cutoff)
        )
        log.info("Deleted %d story_clusters", result.rowcount)

        # Keep the row, drop the payload — re-renderable from its stories.
        result = await session.execute(
            update(NewsletterORM)
            .where(
                NewsletterORM.newsletter_date < article_cutoff,
                NewsletterORM.html_content != "",
            )
            .values(html_content="", email_html_content=None)
        )
        log.info("Stripped HTML from %d newsletters", result.rowcount)

        result = await session.execute(
            delete(PipelineRunORM).where(PipelineRunORM.run_date < article_cutoff)
        )
        log.info("Deleted %d pipeline_runs", result.rowcount)

        result = await session.execute(
            delete(FailedGenerationORM).where(
                FailedGenerationORM.run_date < article_cutoff
            )
        )
        log.info("Deleted %d failed_generations", result.rowcount)
        # get_session() commits on exit.

    if vacuum:
        await _vacuum()
    else:
        log.info("Skipped VACUUM — deleted rows still occupy disk until one runs.")

    log.info("Archive complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Age out old articles, clusters and stories on a tiered schedule"
    )
    parser.add_argument(
        "--article-days", type=int, default=settings.archive_keep_articles_days,
        help=f"Retention for articles, raw text and embeddings "
             f"(default: {settings.archive_keep_articles_days} from ARCHIVE_KEEP_ARTICLES_DAYS)",
    )
    parser.add_argument(
        "--story-days", type=int, default=settings.archive_keep_stories_days,
        help=f"Retention for knowledge stories and clusters "
             f"(default: {settings.archive_keep_stories_days} from ARCHIVE_KEEP_STORIES_DAYS)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report only; delete nothing")
    parser.add_argument("--force", action="store_true",
                        help="Proceed even when most of the article table would be deleted")
    parser.add_argument("--no-vacuum", action="store_true",
                        help="Skip VACUUM ANALYZE (space is not reclaimed)")
    args = parser.parse_args()

    asyncio.run(archive(
        article_days=args.article_days,
        story_days=args.story_days,
        dry_run=args.dry_run,
        force=args.force,
        vacuum=not args.no_vacuum,
    ))
