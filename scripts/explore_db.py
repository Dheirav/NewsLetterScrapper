"""
scripts/explore_db.py
----------------------
Interactive database explorer for the Intelligence Briefing system.

Shows a rich summary of everything in the DB and lets you browse records.

Usage:
    python scripts/explore_db.py                  # overview
    python scripts/explore_db.py --stories        # list knowledge stories
    python scripts/explore_db.py --clusters       # list clusters
    python scripts/explore_db.py --articles       # list recent articles
    python scripts/explore_db.py --story 42       # full knowledge story by id
    python scripts/explore_db.py --cluster <uuid> # cluster detail
    python scripts/explore_db.py --stats          # DB row counts by table
    python scripts/explore_db.py --sources        # article count by source
    python scripts/explore_db.py --days 14        # limit to last N days (default 7)
"""
import argparse
import asyncio
import json
import logging
import sys
import textwrap
from datetime import date, timedelta
from pathlib import Path

# Silence SQLAlchemy echo BEFORE importing anything from core
import os
os.environ.setdefault("APP_ENV", "production")
logging.basicConfig(level=logging.WARNING)
logging.getLogger("sqlalchemy").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select, text

from core.db.orm_models import (
    ArticleORM,
    KnowledgeStoryORM,
    NewsletterORM,
    StoryClusterORM,
)
from core.db.session import get_session

# ── terminal colours (graceful fallback) ────────────────────────────────────

try:
    from colorama import Fore, Style, init as colorama_init
    colorama_init()
    C = {"head": Fore.CYAN + Style.BRIGHT, "dim": Fore.WHITE + Style.DIM,
         "ok": Fore.GREEN, "warn": Fore.YELLOW, "err": Fore.RED,
         "val": Fore.WHITE, "reset": Style.RESET_ALL}
except ImportError:
    C = {k: "" for k in ("head","dim","ok","warn","err","val","reset")}


SEP = "─" * 72


def _h(text_: str) -> str:
    return f"{C['head']}{text_}{C['reset']}"

def _d(text_: str) -> str:
    return f"{C['dim']}{text_}{C['reset']}"

def _ok(text_: str) -> str:
    return f"{C['ok']}{text_}{C['reset']}"

def _v(text_: str) -> str:
    return f"{C['val']}{text_}{C['reset']}"

def _print_sep():
    print(_d(SEP))


# ── Commands ─────────────────────────────────────────────────────────────────

async def cmd_stats():
    async with get_session() as session:
        counts = {}
        for label, model in [
            ("articles", ArticleORM),
            ("story_clusters", StoryClusterORM),
            ("knowledge_stories", KnowledgeStoryORM),
            ("newsletters", NewsletterORM),
        ]:
            res = await session.execute(select(func.count()).select_from(model))
            counts[label] = res.scalar()

        print()
        print(_h("  DATABASE STATISTICS"))
        _print_sep()
        for label, count in counts.items():
            print(f"  {label:<25} {_ok(str(count)):>10}")
        _print_sep()
        print()


async def cmd_overview(days: int):
    since = date.today() - timedelta(days=days)
    async with get_session() as session:
        # Recent clusters
        cl_res = await session.execute(
            select(StoryClusterORM)
            .where(StoryClusterORM.cluster_date >= since)
            .order_by(StoryClusterORM.cluster_date.desc())
            .limit(20)
        )
        clusters = cl_res.scalars().all()

        # Stories count
        st_res = await session.execute(
            select(func.count())
            .select_from(KnowledgeStoryORM)
            .where(KnowledgeStoryORM.story_date >= since)
        )
        story_count = st_res.scalar()

        # Newsletters
        nl_res = await session.execute(
            select(NewsletterORM)
            .where(NewsletterORM.newsletter_date >= since)
            .order_by(NewsletterORM.newsletter_date.desc())
        )
        newsletters = nl_res.scalars().all()

    print()
    print(_h(f"  OVERVIEW — last {days} days  (since {since})"))
    _print_sep()
    print(f"  Clusters:          {_ok(str(len(clusters)))}")
    print(f"  Knowledge stories: {_ok(str(story_count))}")
    print(f"  Newsletters:       {_ok(str(len(newsletters)))}")
    _print_sep()

    if clusters:
        print(_h("  RECENT CLUSTERS"))
        for c in clusters:
            print(f"  {_d(str(c.cluster_date))}  [{c.article_count:>2} art]  {c.topic_label[:65]}")
        _print_sep()

    if newsletters:
        print(_h("  NEWSLETTERS"))
        for n in newsletters:
            sent_flag = _ok("✓ sent") if n.sent else _d("unsent")
            print(f"  {str(n.newsletter_date)}  {sent_flag}  id={n.id}")
        _print_sep()
    print()


async def cmd_articles(days: int):
    since = date.today() - timedelta(days=days)
    async with get_session() as session:
        res = await session.execute(
            select(ArticleORM)
            .where(ArticleORM.created_at >= func.now() - text(f"INTERVAL '{days} days'"))
            .order_by(ArticleORM.created_at.desc())
            .limit(100)
        )
        articles = res.scalars().all()

    print()
    print(_h(f"  ARTICLES — last {days} days  ({len(articles)} shown, max 100)"))
    _print_sep()
    for a in articles:
        cluster_tag = _d(f"[→{a.cluster_id[:8]}]") if a.cluster_id else _d("[unclust]")
        src = f"{a.source[:18]:<18}"
        print(f"  id={a.id:<6} {cluster_tag} {src}  {a.title[:55]}")
    _print_sep()
    print()


async def cmd_clusters(days: int):
    since = date.today() - timedelta(days=days)
    async with get_session() as session:
        res = await session.execute(
            select(StoryClusterORM)
            .where(StoryClusterORM.cluster_date >= since)
            .order_by(StoryClusterORM.cluster_date.desc())
        )
        clusters = res.scalars().all()

    print()
    print(_h(f"  CLUSTERS — last {days} days  ({len(clusters)} total)"))
    _print_sep()
    for c in clusters:
        print(f"  {_d(str(c.cluster_date))}  [{c.article_count:>2}]  {_d(c.cluster_id[:8]+'…')}  {c.topic_label[:60]}")
    _print_sep()
    print()


async def cmd_stories(days: int):
    since = date.today() - timedelta(days=days)
    async with get_session() as session:
        res = await session.execute(
            select(KnowledgeStoryORM)
            .where(KnowledgeStoryORM.story_date >= since)
            .order_by(KnowledgeStoryORM.story_date.desc())
        )
        stories = res.scalars().all()

    print()
    print(_h(f"  KNOWLEDGE STORIES — last {days} days  ({len(stories)} total)"))
    _print_sep()
    for s in stories:
        print(f"  id={s.id:<6}  {_d(str(s.story_date))}  sources={s.source_count}  {s.topic_label[:58]}")
    _print_sep()
    print()


async def cmd_story_detail(story_id: int):
    async with get_session() as session:
        res = await session.execute(
            select(KnowledgeStoryORM).where(KnowledgeStoryORM.id == story_id)
        )
        s = res.scalar_one_or_none()

    if not s:
        print(f"Story {story_id} not found.")
        return

    def section(label, body):
        print()
        print(_h(f"  {label}"))
        _print_sep()
        print(f"  {body}")

    print()
    print(_h(f"  KNOWLEDGE STORY  id={s.id}"))
    print(f"  {_d(s.topic_label)}")
    _print_sep()
    section("EXECUTIVE SUMMARY", s.executive_summary)
    section("CONTEXT", s.context)
    section("WHY IT MATTERS", s.why_it_matters)
    section("IMPLICATIONS", s.implications)

    pts = json.loads(s.talking_points) if s.talking_points else []
    if pts:
        print()
        print(_h("  TALKING POINTS"))
        _print_sep()
        for i, pt in enumerate(pts, 1):
            print(f"  {i}. {pt}")

    if s.reliability_notes:
        print()
        print(_h("  RELIABILITY"))
        _print_sep()
        print(f"  {s.reliability_notes}")

    sources = json.loads(s.article_sources) if s.article_sources else []
    urls = json.loads(s.article_urls) if s.article_urls else []
    if sources:
        print()
        print(_h("  SOURCES"))
        _print_sep()
        for src, url in zip(sources, urls):
            print(f"  {_ok(src):<25}  {_d(url[:60])}")

    _print_sep()
    print()


async def cmd_cluster_detail(cluster_id: str):
    async with get_session() as session:
        cl_res = await session.execute(
            select(StoryClusterORM).where(StoryClusterORM.cluster_id == cluster_id)
        )
        c = cl_res.scalar_one_or_none()
        if not c:
            # Try prefix search
            cl_res = await session.execute(
                select(StoryClusterORM).where(StoryClusterORM.cluster_id.startswith(cluster_id))
            )
            c = cl_res.scalar_one_or_none()

        if not c:
            print(f"Cluster '{cluster_id}' not found.")
            return

        ar_res = await session.execute(
            select(ArticleORM).where(ArticleORM.cluster_id == c.cluster_id)
        )
        articles = ar_res.scalars().all()

        st_res = await session.execute(
            select(KnowledgeStoryORM).where(KnowledgeStoryORM.cluster_id == c.cluster_id)
        )
        story = st_res.scalar_one_or_none()

    print()
    print(_h(f"  CLUSTER  {c.cluster_id}"))
    print(f"  {c.topic_label}")
    _print_sep()
    print(f"  Date: {c.cluster_date}    Articles: {c.article_count}    "
          f"Story: {'id='+str(story.id) if story else 'none'}")
    _print_sep()
    print(_h("  MEMBER ARTICLES"))
    for a in articles:
        print(f"  id={a.id:<6}  {a.source:<20}  {a.title[:50]}")
    _print_sep()
    print()


async def cmd_sources(days: int):
    async with get_session() as session:
        res = await session.execute(
            select(ArticleORM.source, func.count(ArticleORM.id).label("n"))
            .where(ArticleORM.created_at >= func.now() - text(f"INTERVAL '{days} days'"))
            .group_by(ArticleORM.source)
            .order_by(func.count(ArticleORM.id).desc())
        )
        rows = res.all()

    print()
    print(_h(f"  ARTICLES BY SOURCE — last {days} days"))
    _print_sep()
    for source, n in rows:
        bar = "█" * min(40, n)
        print(f"  {source:<28}  {_ok(str(n)):>6}  {_d(bar)}")
    _print_sep()
    print()


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Explore the Intelligence Briefing database",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--days", type=int, default=7, help="Lookback window in days")
    parser.add_argument("--stats", action="store_true", help="Row counts per table")
    parser.add_argument("--articles", action="store_true", help="List recent articles")
    parser.add_argument("--clusters", action="store_true", help="List clusters")
    parser.add_argument("--stories", action="store_true", help="List knowledge stories")
    parser.add_argument("--story", type=int, metavar="ID", help="Full detail for a story")
    parser.add_argument("--cluster", type=str, metavar="UUID", help="Cluster detail (UUID prefix ok)")
    parser.add_argument("--sources", action="store_true", help="Article count by source")
    args = parser.parse_args()

    if args.stats:
        asyncio.run(cmd_stats())
    elif args.articles:
        asyncio.run(cmd_articles(args.days))
    elif args.clusters:
        asyncio.run(cmd_clusters(args.days))
    elif args.stories:
        asyncio.run(cmd_stories(args.days))
    elif args.story:
        asyncio.run(cmd_story_detail(args.story))
    elif args.cluster:
        asyncio.run(cmd_cluster_detail(args.cluster))
    elif args.sources:
        asyncio.run(cmd_sources(args.days))
    else:
        asyncio.run(cmd_overview(args.days))


if __name__ == "__main__":
    main()
