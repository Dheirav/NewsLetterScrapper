"""
Repository round-trips against real PostgreSQL.

These exercise the SQL itself — upserts, predicates, FK ordering — which the
stubbed unit tests cannot reach. Several findings in the original audit lived
exactly here: a delete predicate that could never match orphaned rows, an
upsert whose date came from the wrong clock, a mutation that reported success
for rows that did not exist.
"""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

from core.db.orm_models import ArticleORM, KnowledgeStoryORM, StoryClusterORM
from core.schemas.models import Article, KnowledgeStory, Newsletter, StoryCluster
from services.ingestion.deduplicator import deduplicate
from services.ingestion.repository import save_articles
from services.knowledge.repository import get_stories_for_date, save_knowledge_story
from services.newsletter.repository import (
    get_newsletter_for_date,
    mark_sent,
    save_newsletter,
)
from services.newsletter.unsubscribe import (
    get_all_opted_out,
    is_opted_out,
    make_token,
    opt_out,
    verify_token,
)
from services.understanding.repository import save_clusters

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

RUN_DATE = date(2026, 8, 17)


def _article(n, **kw):
    return Article(
        title=kw.get("title", f"Article {n}"),
        source=kw.get("source", "Reuters"),
        url=kw.get("url", f"https://example.com/{n}"),
        published_at=datetime.now(tz=timezone.utc),
        content=kw.get("content", f"Body of article {n}. " * 20),
        source_type=kw.get("source_type", "news"),
        source_weight=kw.get("source_weight", 1.0),
    )


# ── Articles ─────────────────────────────────────────────────────────────────

async def test_save_articles_assigns_ids(clean_tables):
    session = clean_tables
    saved = await save_articles([_article(1), _article(2)], session)
    assert all(a.id for a in saved)
    assert len({a.id for a in saved}) == 2


async def test_save_articles_is_idempotent_on_url(clean_tables):
    """The pipeline re-runs; a repeated URL must not raise or duplicate."""
    session = clean_tables
    first = await save_articles([_article(1)], session)
    second = await save_articles([_article(1)], session)

    assert first[0].id == second[0].id
    count = (await session.execute(text("SELECT count(*) FROM articles"))).scalar()
    assert count == 1


async def test_source_metadata_survives_the_round_trip(clean_tables):
    session = clean_tables
    await save_articles(
        [_article(1, source="Brookings", source_type="analysis", source_weight=0.7)],
        session,
    )
    row = (await session.execute(select(ArticleORM))).scalar_one()
    assert row.source_type == "analysis"
    assert row.source_weight == pytest.approx(0.7)


async def test_content_quality_default_is_applied_by_the_database(clean_tables):
    session = clean_tables
    await save_articles([_article(1)], session)
    row = (await session.execute(select(ArticleORM))).scalar_one()
    assert row.content_quality == "ok"


# ── Deduplication ────────────────────────────────────────────────────────────

async def test_deduplicate_filters_urls_already_stored(clean_tables):
    session = clean_tables
    await save_articles([_article(1), _article(2)], session)

    incoming = [_article(1), _article(3, title="Something entirely different")]
    fresh = await deduplicate(incoming, session)

    assert [a.url for a in fresh] == ["https://example.com/3"]


async def test_deduplicate_collapses_near_identical_titles_in_one_batch(clean_tables):
    session = clean_tables
    incoming = [
        _article(1, title="Tariff talks stall between US and China", url="https://a.com/1"),
        _article(2, title="Tariff talks stall between US and China.", url="https://a.com/2"),
        _article(3, title="Completely unrelated olive oil study", url="https://a.com/3"),
    ]
    fresh = await deduplicate(incoming, session)
    assert len(fresh) == 2


# ── Clusters and stories ─────────────────────────────────────────────────────

async def test_save_clusters_links_articles_and_stamps_run_date(clean_tables):
    session = clean_tables
    articles = await save_articles([_article(1), _article(2)], session)
    cluster = StoryCluster(
        cluster_id="cluster-a", topic_label="Tariff talks",
        articles=articles, created_at=datetime.now(tz=timezone.utc),
    )

    await save_clusters([cluster], session, run_date=RUN_DATE)

    orm = (await session.execute(select(StoryClusterORM))).scalar_one()
    assert orm.cluster_date == RUN_DATE
    assert orm.article_count == 2

    linked = (await session.execute(
        select(ArticleORM).where(ArticleORM.cluster_id == "cluster-a")
    )).scalars().all()
    assert len(linked) == 2


async def test_save_clusters_is_rerunnable(clean_tables):
    session = clean_tables
    articles = await save_articles([_article(1)], session)
    cluster = StoryCluster("cluster-a", "First label", articles, datetime.now(tz=timezone.utc))

    await save_clusters([cluster], session, run_date=RUN_DATE)
    cluster.topic_label = "Relabelled"
    await save_clusters([cluster], session, run_date=RUN_DATE)

    rows = (await session.execute(select(StoryClusterORM))).scalars().all()
    assert len(rows) == 1
    assert rows[0].topic_label == "Relabelled"


def _story(cluster_id="cluster-a"):
    return KnowledgeStory(
        cluster_id=cluster_id, topic_label="Tariff talks",
        executive_summary="Summary.", context="Context.",
        why_it_matters="Matters.", implications="Implications.",
        talking_points=["one", "two"], reliability_notes="Notes.",
        source_count=2, article_urls=["https://example.com/1"],
        article_sources=["Reuters"],
    )


async def test_story_round_trip_preserves_jsonb_fields(clean_tables):
    session = clean_tables
    await save_articles([_article(1)], session)
    await save_clusters(
        [StoryCluster("cluster-a", "L", [], datetime.now(tz=timezone.utc))],
        session, run_date=RUN_DATE,
    )
    await save_knowledge_story(_story(), session, RUN_DATE)

    loaded = await get_stories_for_date(RUN_DATE, session)
    assert len(loaded) == 1
    assert loaded[0].talking_points == ["one", "two"]
    assert loaded[0].article_sources == ["Reuters"]


async def test_story_upsert_does_not_duplicate_on_rerun(clean_tables):
    session = clean_tables
    await save_clusters(
        [StoryCluster("cluster-a", "L", [], datetime.now(tz=timezone.utc))],
        session, run_date=RUN_DATE,
    )
    await save_knowledge_story(_story(), session, RUN_DATE)
    story = _story()
    story.executive_summary = "Revised summary."
    await save_knowledge_story(story, session, RUN_DATE)

    rows = (await session.execute(select(KnowledgeStoryORM))).scalars().all()
    assert len(rows) == 1
    assert rows[0].executive_summary == "Revised summary."


async def test_stories_are_filed_under_the_run_date_not_today(clean_tables):
    """
    The midnight bug, end to end: a run that started yesterday must remain
    retrievable under yesterday's date even though the clock has moved on.
    """
    session = clean_tables
    yesterday = date.today() - timedelta(days=1)
    await save_clusters(
        [StoryCluster("cluster-a", "L", [], datetime.now(tz=timezone.utc))],
        session, run_date=yesterday,
    )
    await save_knowledge_story(_story(), session, yesterday)

    assert len(await get_stories_for_date(yesterday, session)) == 1
    assert len(await get_stories_for_date(date.today(), session)) == 0


# ── Newsletters ──────────────────────────────────────────────────────────────

async def test_newsletter_save_and_fetch(clean_tables):
    session = clean_tables
    nl = Newsletter(date=RUN_DATE, stories=[], html_content="<h1>web</h1>",
                    email_html_content="<h1>email</h1>")
    await save_newsletter(nl, session)

    orm = await get_newsletter_for_date(RUN_DATE, session)
    assert orm.html_content == "<h1>web</h1>"
    assert orm.email_html_content == "<h1>email</h1>"
    assert orm.sent is False


async def test_newsletter_save_replaces_rather_than_duplicating(clean_tables):
    session = clean_tables
    await save_newsletter(Newsletter(RUN_DATE, [], "<p>v1</p>"), session)
    await save_newsletter(Newsletter(RUN_DATE, [], "<p>v2</p>"), session)

    count = (await session.execute(text("SELECT count(*) FROM newsletters"))).scalar()
    assert count == 1
    orm = await get_newsletter_for_date(RUN_DATE, session)
    assert orm.html_content == "<p>v2</p>"


async def test_empty_newsletter_is_rejected(clean_tables):
    with pytest.raises(ValueError):
        await save_newsletter(Newsletter(RUN_DATE, [], ""), clean_tables)


async def test_mark_sent_records_a_timestamp(clean_tables):
    session = clean_tables
    nl = Newsletter(RUN_DATE, [], "<p>x</p>")
    orm = await save_newsletter(nl, session)
    await mark_sent(orm.id, session)

    refreshed = await get_newsletter_for_date(RUN_DATE, session)
    assert refreshed.sent is True
    assert refreshed.sent_at is not None


# ── Unsubscribe ──────────────────────────────────────────────────────────────

async def test_opt_out_round_trip(clean_tables):
    session = clean_tables
    assert await is_opted_out("alice@example.com", session) is False
    assert await opt_out("alice@example.com", session) is True
    assert await is_opted_out("alice@example.com", session) is True


async def test_opt_out_is_idempotent_and_case_insensitive(clean_tables):
    session = clean_tables
    assert await opt_out("Alice@Example.com", session) is True
    assert await opt_out("alice@example.com", session) is False
    assert await is_opted_out("ALICE@EXAMPLE.COM", session) is True
    assert await get_all_opted_out(session) == ["alice@example.com"]


async def test_token_round_trip_against_stored_opt_out(clean_tables):
    session = clean_tables
    email = "bob@example.com"
    token = make_token(email)
    assert verify_token(email, token) is True
    assert verify_token(email, "forged") is False
    assert verify_token("carol@example.com", token) is False

    await opt_out(email, session)
    assert email in await get_all_opted_out(session)
