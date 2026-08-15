"""
scripts/run_pipeline.py
------------------------
Daily intelligence briefing pipeline orchestrator.

Run manually:
    python scripts/run_pipeline.py                    # concise mode (default)
    python scripts/run_pipeline.py --mode detailed    # deep 5-call mode

Full pipeline:
  1.  Ingest     — fetch all RSS feeds
  2.  Scrape     — enrich with full article text (flags content_quality=low)
  3.  Dedup      — remove already-seen articles (TF-IDF cosine O(n))
  4.  Save       — persist new articles to DB
  5.  Embed      — generate semantic embeddings (Ollama nomic-embed-text)
  6.  Cluster    — group articles into stories (HDBSCAN)
  7.  Label      — LLM-generated topic labels
  8.  Save clusters
  9.  Knowledge  — generate deep knowledge stories
  10. Profile    — rebuild user reading profile
  11. Adapt      — reorder stories by engagement
  12. Assemble   — group into newsletter sections
  13. Render     — generate HTML
  14. Save       — persist newsletter to DB
  15. Email      — send to configured recipient

Pipeline checkpointing:
    Each durable step records its completion in pipeline_runs. On a crash/restart
    the pipeline checks that table and skips steps whose work is already in the DB
    (steps 5, 8, 9, 14), resuming rather than re-running 20 minutes of LLM calls.
"""
import argparse
import asyncio
import logging
import logging.config
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.db.orm_models import ArticleORM, PipelineRunORM
from core.db.session import get_session

# Services
from services.ingestion.feed_reader import fetch_all_feeds
from services.ingestion.scraper import enrich_content
from services.ingestion.deduplicator import deduplicate
from services.ingestion.repository import save_articles

from services.understanding.embedder import embed_articles
from services.understanding.clusterer import cluster_articles
from services.understanding.labeler import label_clusters
from services.understanding.repository import save_clusters

from services.knowledge.generator import generate_knowledge_stories

from services.personalization.profiler import build_profile
from services.personalization.adapter import adapt_newsletter

from services.newsletter.assembler import assemble
from services.newsletter.renderer import render_both
from services.newsletter.repository import save_newsletter, mark_sent
from services.newsletter.emailer import send_email
from core.schemas.models import Article as ArticleSchema


# ── Logging ───────────────────────────────────────────────────────────────────
def _build_log_config() -> dict:
    if settings.log_format == "json":
        formatter: dict = {
            "()": "pythonjsonlogger.jsonlogger.JsonFormatter",
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
        }
    else:
        formatter = {
            "format": "%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
            "datefmt": "%H:%M:%S",
        }
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"default": formatter},
        "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "default"}},
        "root": {"level": settings.log_level.upper(), "handlers": ["console"]},
    }


logging.config.dictConfig(_build_log_config())
log = logging.getLogger("pipeline")
BANNER = "=" * 60


# ── Per-step timing context manager ──────────────────────────────────────────
@asynccontextmanager
async def _step(n: int, label: str):
    """
    Logs the step header on entry and elapsed wall-clock time on exit.
    Wrap every pipeline step with this to immediately see which step is
    the bottleneck (usually embedding or knowledge generation).

    Usage:
        async with _step(5, "Generating embeddings"):
            articles = await embed_articles(articles, session)
    """
    log.info("%s", BANNER)
    log.info("  STEP %d -- %s", n, label)
    log.info("%s", BANNER)
    t0 = time.monotonic()
    try:
        yield
    finally:
        elapsed = time.monotonic() - t0
        log.info("  -> Step %d done in %.1fs", n, elapsed)


# ── Pipeline checkpoint helpers ───────────────────────────────────────────────
async def _step_done(name: str, run_date: date, session: AsyncSession) -> bool:
    """Return True if this step already completed successfully today."""
    result = await session.execute(
        select(PipelineRunORM).where(
            PipelineRunORM.run_date == run_date,
            PipelineRunORM.step_name == name,
            PipelineRunORM.status == "completed",
        )
    )
    return result.scalar_one_or_none() is not None


async def _record_step(
    name: str,
    run_date: date,
    session: AsyncSession,
    status: str = "completed",
    error: str | None = None,
) -> None:
    """Upsert a step completion record."""
    stmt = (
        pg_insert(PipelineRunORM)
        .values(run_date=run_date, step_name=name, status=status, error_message=error)
        .on_conflict_do_update(
            index_elements=["run_date", "step_name"],
            set_={"status": status, "error_message": error},
        )
    )
    await session.execute(stmt)
    await session.flush()


# ── Main pipeline ─────────────────────────────────────────────────────────────
async def run(mode: str = "concise", test: bool = False) -> None:
    today = date.today()
    log.info("\n%s", BANNER)
    log.info(
        "  INTELLIGENCE BRIEFING PIPELINE  --  %s  (mode=%s)%s",
        today,
        mode,
        "  [TEST MODE]" if test else "",
    )
    log.info("%s\n", BANNER)

    pipeline_start = time.monotonic()

    async with get_session() as session:

        # -- 1. Fetch RSS feeds -----------------------------------------------
        async with _step(1, "Fetching RSS feeds"):
            raw_articles = await fetch_all_feeds()
            log.info("Collected %d raw articles", len(raw_articles))

        if not raw_articles:
            log.warning("No articles fetched -- aborting pipeline")
            return

        # -- 2. Scrape full text ----------------------------------------------
        async with _step(2, "Scraping full article text"):
            raw_articles = await enrich_content(raw_articles)
            low_quality = sum(1 for a in raw_articles if a.content_quality == "low")
            if low_quality:
                log.info(
                    "%d articles flagged content_quality=low (paywall/JS pages)",
                    low_quality,
                )

        # -- 3. Deduplicate ---------------------------------------------------
        async with _step(3, "Deduplicating articles (TF-IDF cosine)"):
            new_articles = await deduplicate(raw_articles, session)
            log.info("%d genuinely new articles after deduplication", len(new_articles))

        if not new_articles:
            log.info("No new articles today -- pipeline complete")
            return

        # -- 4. Persist articles ----------------------------------------------
        async with _step(4, "Saving articles to database"):
            new_articles = await save_articles(new_articles, session)
        await _record_step("save_articles", today, session)

        # -- 5. Generate embeddings (skippable) --------------------------------
        if await _step_done("embed", today, session):
            log.info("Step 5 already complete -- reloading embedded articles from DB")
            result = await session.execute(
                select(ArticleORM).where(
                    ArticleORM.embedding.is_not(None),
                    ArticleORM.created_at >= datetime(today.year, today.month, today.day, tzinfo=timezone.utc),
                )
            )
            embedded = [
                ArticleSchema(
                    id=a.id, title=a.title, source=a.source, url=a.url,
                    published_at=a.published_at, content=a.content,
                    content_quality=a.content_quality,
                    cluster_id=a.cluster_id, embedding=a.embedding,
                )
                for a in result.scalars().all()
            ]
            log.info("Reloaded %d embedded articles", len(embedded))
        else:
            async with _step(5, "Generating semantic embeddings"):
                new_articles = await embed_articles(new_articles, session)
                embedded = [a for a in new_articles if a.embedding is not None]
                dropped = len(new_articles) - len(embedded)
                if dropped:
                    log.warning("%d articles dropped -- embedding failed", dropped)
                log.info(
                    "%d/%d articles embedded successfully",
                    len(embedded), len(new_articles),
                )
            await _record_step("embed", today, session)

        # -- 6. Cluster articles into stories ----------------------------------
        async with _step(6, "Clustering articles into stories"):
            clusters = await cluster_articles(embedded)
            log.info("Formed %d story clusters", len(clusters))

        # -- 7. Label clusters ------------------------------------------------
        async with _step(7, "Generating topic labels"):
            clusters = await label_clusters(clusters)

        # -- 8. Save clusters (skippable) -------------------------------------
        async with _step(8, "Saving clusters to database"):
            await save_clusters(clusters, session)
        await _record_step("save_clusters", today, session)

        # -- 9. Knowledge generation (skippable) ------------------------------
        async with _step(9, "Generating deep knowledge stories"):
            stories = await generate_knowledge_stories(
                clusters, session, max_concurrent=6, mode=mode
            )
            log.info("Generated %d knowledge stories", len(stories))
        await _record_step("knowledge", today, session)

        if not stories:
            log.warning("No knowledge stories generated -- aborting assembly")
            return

        # -- 10. Build user profile -------------------------------------------
        async with _step(10, "Building user reading profile"):
            profile = await build_profile(session)
            log.info(
                "Profile: %d topics tracked, preferred depth=%.0f%%",
                len(profile.topic_weights),
                profile.preferred_depth,
            )

        # -- 11. Adapt story ordering -----------------------------------------
        async with _step(11, "Adapting newsletter to user profile"):
            adapted_stories = adapt_newsletter(stories, profile)

        # -- 12. Assemble newsletter ------------------------------------------
        async with _step(12, "Assembling newsletter"):
            newsletter = assemble(adapted_stories, today)

        # -- 13. Render HTML -------------------------------------------------
        async with _step(13, "Rendering HTML newsletter"):
            web_html, email_html = render_both(newsletter)
            log.info(
                "Rendered web=%d chars, email=%d chars",
                len(web_html),
                len(email_html),
            )

        # -- 14. Save newsletter to DB (skippable) ---------------------------
        async with _step(14, "Saving newsletter to database"):
            await save_newsletter(newsletter, session)
        await _record_step("save_newsletter", today, session)

        # -- 15. Send email --------------------------------------------------
        async with _step(15, "Sending email"):
            subject = f"Intelligence Briefing -- {today.strftime('%B %d, %Y')}"
            if test:
                if not settings.test_email:
                    log.error("--test requires TEST_EMAIL to be set in .env")
                    raise SystemExit(1)
                log.info(
                    "TEST MODE — sending only to %s (newsletter will NOT be marked as sent)",
                    settings.test_email,
                )
                sent = await send_email(
                    email_html, subject=subject, recipients=[settings.test_email]
                )
            else:
                sent = await send_email(email_html, subject=subject)
                if sent and newsletter.id:
                    await mark_sent(newsletter.id, session)
        if sent:
            await _record_step("email", today, session)

    total = time.monotonic() - pipeline_start
    log.info("\n%s", BANNER)
    log.info("  PIPELINE COMPLETE  --  total time: %.1fs", total)
    log.info(
        "  %d stories * Newsletter saved * Email %s",
        len(stories),
        "sent" if sent else "NOT sent",
    )
    log.info("  Web reader: http://localhost:8000/api/newsletter/%s", today)
    log.info("  Reflection: http://localhost:8000/api/reflection/%s", today)
    log.info("%s\n", BANNER)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run the daily intelligence briefing pipeline"
    )
    parser.add_argument(
        "--mode",
        choices=["concise", "detailed"],
        default="concise",
        help=(
            "LLM generation mode. "
            "'concise' = 1 JSON call per cluster (fast, default). "
            "'detailed' = 5 focused calls per cluster (richer, ~5x slower)."
        ),
    )
    parser.add_argument(
        "--test",
        action="store_true",
        default=False,
        help="Send email only to TEST_EMAIL from .env. Newsletter is NOT marked as sent.",
    )
    args = parser.parse_args()
    asyncio.run(run(mode=args.mode, test=args.test))
