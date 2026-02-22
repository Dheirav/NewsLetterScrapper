"""
scripts/crud_db.py
-------------------
Command-line CRUD tool for the Intelligence Briefing database.

Usage
-----
  # Articles
  python scripts/crud_db.py articles list [--days N] [--source NAME]
  python scripts/crud_db.py articles get <id>
  python scripts/crud_db.py articles update <id> --title "…" --content "…"
  python scripts/crud_db.py articles delete <id>

  # Clusters
  python scripts/crud_db.py clusters list [--days N]
  python scripts/crud_db.py clusters get <uuid>
  python scripts/crud_db.py clusters rename <uuid> "New topic label"
  python scripts/crud_db.py clusters delete <uuid>   # cascades to story

  # Knowledge stories
  python scripts/crud_db.py stories list [--days N]
  python scripts/crud_db.py stories get <id>
  python scripts/crud_db.py stories update <id> --summary "…" --context "…"
  python scripts/crud_db.py stories delete <id>

  # Newsletters
  python scripts/crud_db.py newsletters list
  python scripts/crud_db.py newsletters get <id>
  python scripts/crud_db.py newsletters delete <id>
"""
import argparse
import asyncio
import json
import sys
import textwrap
from datetime import date, timedelta
from pathlib import Path

import logging
logging.basicConfig(level=logging.WARNING)
logging.getLogger("sqlalchemy").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

import os
os.environ.setdefault("APP_ENV", "production")  # suppress SQLAlchemy echo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select, update, func, text

from core.db.orm_models import (
    ArticleORM,
    KnowledgeStoryORM,
    NewsletterORM,
    StoryClusterORM,
)
from core.db.session import get_session

SEP = "─" * 60


def _confirm(prompt: str) -> bool:
    ans = input(f"{prompt} [y/N] ").strip().lower()
    return ans in ("y", "yes")


def _wrap(s: str, width: int = 90, indent: int = 4) -> str:
    prefix = " " * indent
    return textwrap.fill(str(s or ""), width=width, initial_indent=prefix, subsequent_indent=prefix)


# ═══════════════════════════════════════════════════════
# ARTICLES
# ═══════════════════════════════════════════════════════

async def articles_list(args):
    since = date.today() - timedelta(days=args.days)
    async with get_session() as session:
        stmt = (
            select(ArticleORM)
            .where(ArticleORM.created_at >= func.now() - text(f"INTERVAL '{args.days} days'"))
            .order_by(ArticleORM.created_at.desc())
            .limit(200)
        )
        if args.source:
            stmt = stmt.where(ArticleORM.source == args.source)
        res = await session.execute(stmt)
        rows = res.scalars().all()

    print(f"\n  {'ID':<7} {'SOURCE':<22} {'TITLE'}")
    print(SEP)
    for r in rows:
        print(f"  {r.id:<7} {r.source[:20]:<22} {r.title[:55]}")
    print(f"\n  {len(rows)} articles\n")


async def articles_get(args):
    async with get_session() as session:
        res = await session.execute(select(ArticleORM).where(ArticleORM.id == args.id))
        row = res.scalar_one_or_none()
    if not row:
        print(f"Article {args.id} not found.")
        return
    print(f"\n  id       : {row.id}")
    print(f"  title    : {row.title}")
    print(f"  source   : {row.source}")
    print(f"  url      : {row.url}")
    print(f"  published: {row.published_at}")
    print(f"  cluster  : {row.cluster_id}")
    print(f"\n  content preview:\n")
    print(_wrap((row.content or "")[:600]))
    print()


async def articles_update(args):
    values = {}
    if args.title:
        values["title"] = args.title
    if args.content:
        values["content"] = args.content
    if args.source:
        values["source"] = args.source
    if not values:
        print("Nothing to update. Provide --title, --content, or --source.")
        return
    async with get_session() as session:
        await session.execute(
            update(ArticleORM).where(ArticleORM.id == args.id).values(**values)
        )
    print(f"  Updated article {args.id}: {list(values.keys())}")


async def articles_delete(args):
    if not _confirm(f"Delete article {args.id}?"):
        print("  Aborted.")
        return
    async with get_session() as session:
        await session.execute(delete(ArticleORM).where(ArticleORM.id == args.id))
    print(f"  Deleted article {args.id}.")


# ═══════════════════════════════════════════════════════
# CLUSTERS
# ═══════════════════════════════════════════════════════

async def _find_cluster(session, uuid_prefix: str):
    res = await session.execute(
        select(StoryClusterORM).where(StoryClusterORM.cluster_id == uuid_prefix)
    )
    c = res.scalar_one_or_none()
    if not c:
        res = await session.execute(
            select(StoryClusterORM).where(StoryClusterORM.cluster_id.startswith(uuid_prefix))
        )
        c = res.scalar_one_or_none()
    return c


async def clusters_list(args):
    since = date.today() - timedelta(days=args.days)
    async with get_session() as session:
        res = await session.execute(
            select(StoryClusterORM)
            .where(StoryClusterORM.cluster_date >= since)
            .order_by(StoryClusterORM.cluster_date.desc())
        )
        rows = res.scalars().all()
    print(f"\n  {'DATE':<12} {'ART':>4}  {'UUID PREFIX':<12}  TOPIC")
    print(SEP)
    for r in rows:
        print(f"  {str(r.cluster_date):<12} {r.article_count:>4}  {r.cluster_id[:8]+'..':<12}  {r.topic_label[:55]}")
    print(f"\n  {len(rows)} clusters\n")


async def clusters_get(args):
    async with get_session() as session:
        c = await _find_cluster(session, args.uuid)
        if not c:
            print(f"Cluster '{args.uuid}' not found.")
            return
        art_res = await session.execute(
            select(ArticleORM).where(ArticleORM.cluster_id == c.cluster_id)
        )
        articles = art_res.scalars().all()
    print(f"\n  cluster_id : {c.cluster_id}")
    print(f"  topic      : {c.topic_label}")
    print(f"  date       : {c.cluster_date}")
    print(f"  articles   : {c.article_count}")
    print(f"\n  {'ID':<7} {'SOURCE':<22} TITLE")
    print(SEP)
    for a in articles:
        print(f"  {a.id:<7} {a.source[:20]:<22} {a.title[:50]}")
    print()


async def clusters_rename(args):
    async with get_session() as session:
        c = await _find_cluster(session, args.uuid)
        if not c:
            print(f"Cluster '{args.uuid}' not found.")
            return
        await session.execute(
            update(StoryClusterORM)
            .where(StoryClusterORM.cluster_id == c.cluster_id)
            .values(topic_label=args.label)
        )
    print(f"  Renamed cluster {args.uuid[:8]}.. → '{args.label}'")



# ═══════════════════════════════════════════════════════
# STORIES
# ═══════════════════════════════════════════════════════

async def stories_list(args):
    since = date.today() - timedelta(days=args.days)
    async with get_session() as session:
        res = await session.execute(
            select(KnowledgeStoryORM)
            .where(KnowledgeStoryORM.story_date >= since)
            .order_by(KnowledgeStoryORM.story_date.desc())
        )
        rows = res.scalars().all()
    print(f"\n  {'ID':<7} {'DATE':<12} {'SRC':>4}  TOPIC")
    print(SEP)
    for r in rows:
        print(f"  {r.id:<7} {str(r.story_date):<12} {r.source_count:>4}  {r.topic_label[:55]}")
    print(f"\n  {len(rows)} stories\n")


async def stories_get(args):
    async with get_session() as session:
        res = await session.execute(
            select(KnowledgeStoryORM).where(KnowledgeStoryORM.id == args.id)
        )
        s = res.scalar_one_or_none()
    if not s:
        print(f"Story {args.id} not found.")
        return

    pts = json.loads(s.talking_points) if s.talking_points else []
    print(f"\n  id: {s.id}  |  date: {s.story_date}  |  sources: {s.source_count}")
    print(f"  {s.topic_label}\n")
    for label, body in [
        ("EXECUTIVE SUMMARY", s.executive_summary),
        ("CONTEXT", s.context),
        ("WHY IT MATTERS", s.why_it_matters),
        ("IMPLICATIONS", s.implications),
    ]:
        print(f"  {label}")
        print(SEP)
        print(_wrap(body))
        print()

    if pts:
        print("  TALKING POINTS")
        print(SEP)
        for i, pt in enumerate(pts, 1):
            print(f"  {i}. {pt}")
        print()

    if s.reliability_notes:
        print("  RELIABILITY")
        print(SEP)
        print(_wrap(s.reliability_notes))
        print()


async def stories_update(args):
    values: dict = {}
    if args.label:
        values["topic_label"] = args.label
    if args.summary:
        values["executive_summary"] = args.summary
    if args.context:
        values["context"] = args.context
    if args.matters:
        values["why_it_matters"] = args.matters
    if args.implications:
        values["implications"] = args.implications
    if not values:
        print("Nothing to update. Use --label, --summary, --context, --matters, --implications.")
        return
    async with get_session() as session:
        await session.execute(
            update(KnowledgeStoryORM).where(KnowledgeStoryORM.id == args.id).values(**values)
        )
    print(f"  Updated story {args.id}: {list(values.keys())}")


async def stories_delete(args):
    if not _confirm(f"Delete knowledge story {args.id}?"):
        print("  Aborted.")
        return
    async with get_session() as session:
        await session.execute(delete(KnowledgeStoryORM).where(KnowledgeStoryORM.id == args.id))
    print(f"  Deleted story {args.id}.")


# ═══════════════════════════════════════════════════════
# NEWSLETTERS
# ═══════════════════════════════════════════════════════

async def newsletters_list(args):
    async with get_session() as session:
        res = await session.execute(
            select(NewsletterORM).order_by(NewsletterORM.newsletter_date.desc()).limit(30)
        )
        rows = res.scalars().all()
    print(f"\n  {'ID':<7} {'DATE':<14} {'SENT'}")
    print(SEP)
    for r in rows:
        sent = "✓ sent" if r.sent else "unsent"
        print(f"  {r.id:<7} {str(r.newsletter_date):<14} {sent}")
    print()


async def newsletters_get(args):
    async with get_session() as session:
        res = await session.execute(
            select(NewsletterORM).where(NewsletterORM.id == args.id)
        )
        n = res.scalar_one_or_none()
    if not n:
        print(f"Newsletter {args.id} not found.")
        return
    print(f"\n  id   : {n.id}")
    print(f"  date : {n.newsletter_date}")
    print(f"  sent : {'yes' if n.sent else 'no'}")
    print(f"  sent_at: {n.sent_at}")
    print(f"  html preview ({len(n.html_content or '')} chars):")
    print(_wrap((n.html_content or "")[:300]))
    print()


async def newsletters_delete(args):
    if not _confirm(f"Delete newsletter {args.id}?"):
        print("  Aborted.")
        return
    async with get_session() as session:
        from sqlalchemy import delete as sa_delete
        await session.execute(
            sa_delete(NewsletterORM).where(NewsletterORM.id == args.id)
        )
    print(f"  Deleted newsletter {args.id}.")


# ═══════════════════════════════════════════════════════
# CLI routing
# ═══════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        prog="crud_db",
        description="CRUD operations on the Intelligence Briefing database",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="entity", required=True)

    # ── articles ──────────────────────────────────────────────────────────────
    ap = sub.add_parser("articles")
    ap_sub = ap.add_subparsers(dest="action", required=True)

    ap_list = ap_sub.add_parser("list")
    ap_list.add_argument("--days", type=int, default=7)
    ap_list.add_argument("--source", type=str)

    ap_get = ap_sub.add_parser("get")
    ap_get.add_argument("id", type=int)

    ap_upd = ap_sub.add_parser("update")
    ap_upd.add_argument("id", type=int)
    ap_upd.add_argument("--title", type=str)
    ap_upd.add_argument("--content", type=str)
    ap_upd.add_argument("--source", type=str)

    ap_del = ap_sub.add_parser("delete")
    ap_del.add_argument("id", type=int)

    # ── clusters ──────────────────────────────────────────────────────────────
    cp = sub.add_parser("clusters")
    cp_sub = cp.add_subparsers(dest="action", required=True)

    cp_list = cp_sub.add_parser("list")
    cp_list.add_argument("--days", type=int, default=7)

    cp_get = cp_sub.add_parser("get")
    cp_get.add_argument("uuid", type=str)

    cp_ren = cp_sub.add_parser("rename")
    cp_ren.add_argument("uuid", type=str)
    cp_ren.add_argument("label", type=str)

    cp_del = cp_sub.add_parser("delete")
    cp_del.add_argument("uuid", type=str)

    # ── stories ───────────────────────────────────────────────────────────────
    sp = sub.add_parser("stories")
    sp_sub = sp.add_subparsers(dest="action", required=True)

    sp_list = sp_sub.add_parser("list")
    sp_list.add_argument("--days", type=int, default=7)

    sp_get = sp_sub.add_parser("get")
    sp_get.add_argument("id", type=int)

    sp_upd = sp_sub.add_parser("update")
    sp_upd.add_argument("id", type=int)
    sp_upd.add_argument("--label", type=str)
    sp_upd.add_argument("--summary", type=str)
    sp_upd.add_argument("--context", type=str)
    sp_upd.add_argument("--matters", type=str)
    sp_upd.add_argument("--implications", type=str)

    sp_del = sp_sub.add_parser("delete")
    sp_del.add_argument("id", type=int)

    # ── newsletters ───────────────────────────────────────────────────────────
    np_ = sub.add_parser("newsletters")
    np_sub = np_.add_subparsers(dest="action", required=True)
    np_sub.add_parser("list")

    np_get = np_sub.add_parser("get")
    np_get.add_argument("id", type=int)

    np_del = np_sub.add_parser("delete")
    np_del.add_argument("id", type=int)

    args = parser.parse_args()

    dispatch = {
        ("articles",     "list"):    articles_list,
        ("articles",     "get"):     articles_get,
        ("articles",     "update"):  articles_update,
        ("articles",     "delete"):  articles_delete,
        ("clusters",     "list"):    clusters_list,
        ("clusters",     "get"):     clusters_get,
        ("clusters",     "rename"):  clusters_rename,
        ("clusters",     "delete"):  clusters_delete,
        ("stories",      "list"):    stories_list,
        ("stories",      "get"):     stories_get,
        ("stories",      "update"):  stories_update,
        ("stories",      "delete"):  stories_delete,
        ("newsletters",  "list"):    newsletters_list,
        ("newsletters",  "get"):     newsletters_get,
        ("newsletters",  "delete"):  newsletters_delete,
    }

    fn = dispatch.get((args.entity, args.action))
    if not fn:
        parser.print_help()
        return
    asyncio.run(fn(args))


async def clusters_delete(args):
    async with get_session() as session:
        c = await _find_cluster(session, args.uuid)
        if not c:
            print(f"Cluster '{args.uuid}' not found.")
            return
        if not _confirm(f"Delete cluster '{c.topic_label[:50]}' and its knowledge story?"):
            print("  Aborted.")
            return
        await session.execute(
            update(ArticleORM).where(ArticleORM.cluster_id == c.cluster_id).values(cluster_id=None)
        )
        await session.execute(
            delete(KnowledgeStoryORM).where(KnowledgeStoryORM.cluster_id == c.cluster_id)
        )
        await session.execute(
            delete(StoryClusterORM).where(StoryClusterORM.cluster_id == c.cluster_id)
        )
    print(f"  Deleted cluster {c.cluster_id[:8]}..")


if __name__ == "__main__":
    main()
