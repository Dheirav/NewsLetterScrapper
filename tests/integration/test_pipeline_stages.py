"""
Pipeline stages against real PostgreSQL and real pgvector.

Everything here depends on SQL behaviour that a stubbed session cannot model:
cosine distance ordering, a delete predicate reaching NULL columns, an UPDATE
rowcount, FK ordering during a cascade.
"""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

from core.db.orm_models import ArticleORM, NewsletterORM, StoryClusterORM
from core.schemas.models import Article, Newsletter, StoryCluster
from scripts.archive import archive
from services.ingestion.repository import save_articles
from services.ingestion.semantic_dedup import mark_semantic_duplicates
from services.newsletter.repository import save_newsletter
from services.understanding.repository import save_clusters

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

DIM = 768


def _factory(session):
    """Hand archive() the test's own session instead of the global engine."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _f():
        yield session

    return _f


def _vec(kind: int, dim: int = DIM):
    """
    A one-hot vector. Same `kind` -> cosine similarity 1.0; different `kind` ->
    0.0. An earlier version varied only the first element while sharing a large
    identical tail, which made every pair near-identical and quietly broke the
    "distinct articles are left alone" case.
    """
    v = [0.0] * dim
    v[kind % dim] = 1.0
    return v


def _article(n, url=None, embedding=None, created_at=None):
    return Article(
        title=f"Article {n}", source="Reuters",
        url=url or f"https://example.com/{n}",
        published_at=datetime.now(tz=timezone.utc),
        content=f"Body {n}. " * 30,
        embedding=embedding,
    )


async def _store(session, article, embedding=None, created_at=None):
    """Persist an article and optionally backdate it / attach a vector."""
    (saved,) = await save_articles([article], session)
    if embedding is not None:
        await session.execute(
            text("UPDATE articles SET embedding = CAST(:v AS vector) WHERE id = :i"),
            {"v": str(embedding), "i": saved.id},
        )
    if created_at is not None:
        await session.execute(
            text("UPDATE articles SET created_at = :t WHERE id = :i"),
            {"t": created_at, "i": saved.id},
        )
    await session.flush()
    return saved


# ── Semantic dedup, real cosine distance ─────────────────────────────────────

async def test_near_identical_later_article_is_marked(clean_tables):
    session = clean_tables
    now = datetime.now(tz=timezone.utc)

    original = await _store(session, _article(1), _vec(0), now - timedelta(days=2))
    repost = await _store(session, _article(2), _vec(0), now)
    repost.embedding = _vec(0)

    fresh = await mark_semantic_duplicates([repost], session, lookback_days=7)

    assert fresh == []
    row = (await session.execute(
        select(ArticleORM).where(ArticleORM.id == repost.id)
    )).scalar_one()
    assert row.duplicate_of == original.id


async def test_distinct_articles_are_left_alone(clean_tables):
    session = clean_tables
    now = datetime.now(tz=timezone.utc)

    await _store(session, _article(1), _vec(0), now - timedelta(days=2))
    other = await _store(session, _article(2), _vec(500), now)
    other.embedding = _vec(500)

    fresh = await mark_semantic_duplicates([other], session, lookback_days=7)

    assert [a.id for a in fresh] == [other.id]
    row = (await session.execute(
        select(ArticleORM).where(ArticleORM.id == other.id)
    )).scalar_one()
    assert row.duplicate_of is None


async def test_matches_outside_the_lookback_window_are_ignored(clean_tables):
    session = clean_tables
    now = datetime.now(tz=timezone.utc)

    await _store(session, _article(1), _vec(0), now - timedelta(days=30))
    recent = await _store(session, _article(2), _vec(0), now)
    recent.embedding = _vec(0)

    fresh = await mark_semantic_duplicates([recent], session, lookback_days=7)

    assert [a.id for a in fresh] == [recent.id]


async def test_an_earlier_article_is_never_marked_against_a_later_one(clean_tables):
    """
    Ordering matters: the original must be the one that came first, or a story
    could be recorded as a duplicate of its own follow-up.
    """
    session = clean_tables
    now = datetime.now(tz=timezone.utc)

    first = await _store(session, _article(1), _vec(0), now - timedelta(days=1))
    first.embedding = _vec(0)
    await _store(session, _article(2), _vec(0), now)

    fresh = await mark_semantic_duplicates([first], session, lookback_days=7)

    assert [a.id for a in fresh] == [first.id], "the earliest article has no original"


async def test_duplicate_row_is_kept_so_url_dedup_still_sees_it(clean_tables):
    """Deleting would let the same URL be re-fetched and re-embedded forever."""
    session = clean_tables
    now = datetime.now(tz=timezone.utc)

    await _store(session, _article(1), _vec(0), now - timedelta(days=1))
    repost = await _store(session, _article(2), _vec(0), now)
    repost.embedding = _vec(0)

    await mark_semantic_duplicates([repost], session, lookback_days=7)

    still_there = (await session.execute(
        text("SELECT count(*) FROM articles WHERE id = :i"), {"i": repost.id}
    )).scalar()
    assert still_there == 1


# ── Archive predicates ───────────────────────────────────────────────────────

async def test_archive_deletes_articles_that_never_clustered(clean_tables):
    """
    The orphan bug. The old predicate was `cluster_id IN (expired clusters)`,
    which structurally cannot reach rows whose cluster_id is NULL.
    """
    session = clean_tables
    old = datetime.now(tz=timezone.utc) - timedelta(days=200)

    await _store(session, _article(1, url="https://a.com/orphan"), created_at=old)
    orphans = (await session.execute(
        text("SELECT count(*) FROM articles WHERE cluster_id IS NULL")
    )).scalar()
    assert orphans == 1

    await archive(article_days=90, story_days=365, force=True, vacuum=False,
                  session_factory=_factory(session))

    remaining = (await session.execute(text("SELECT count(*) FROM articles"))).scalar()
    assert remaining == 0, "an article with no cluster must still age out"


async def test_archive_keeps_stories_while_dropping_their_articles(clean_tables):
    """Tiered retention: bulky raw material goes, the distilled output stays."""
    session = clean_tables
    old = datetime.now(tz=timezone.utc) - timedelta(days=200)
    recent_story_date = date.today() - timedelta(days=200)

    article = await _store(session, _article(1), created_at=old)
    await save_clusters(
        [StoryCluster("cluster-a", "L", [article], datetime.now(tz=timezone.utc))],
        session, run_date=recent_story_date,
    )

    await archive(article_days=90, story_days=365, force=True, vacuum=False,
                  session_factory=_factory(session))

    articles = (await session.execute(text("SELECT count(*) FROM articles"))).scalar()
    clusters = (await session.execute(text("SELECT count(*) FROM story_clusters"))).scalar()
    assert articles == 0, "past the 90-day article window"
    assert clusters == 1, "still inside the 365-day story window"


async def test_archive_strips_newsletter_html_but_keeps_the_row(clean_tables):
    session = clean_tables
    old_date = date.today() - timedelta(days=200)
    await save_newsletter(
        Newsletter(old_date, [], "<h1>big html</h1>", "<h1>email</h1>"), session
    )

    await archive(article_days=90, story_days=365, force=True, vacuum=False,
                  session_factory=_factory(session))

    row = (await session.execute(select(NewsletterORM))).scalar_one()
    assert row.html_content == ""
    assert row.email_html_content is None
    assert row.newsletter_date == old_date, "metadata row must survive"


async def test_archive_leaves_recent_data_untouched(clean_tables):
    session = clean_tables
    await _store(session, _article(1))
    await save_newsletter(Newsletter(date.today(), [], "<p>today</p>"), session)

    await archive(article_days=90, story_days=365, vacuum=False,
                  session_factory=_factory(session))

    articles = (await session.execute(text("SELECT count(*) FROM articles"))).scalar()
    row = (await session.execute(select(NewsletterORM))).scalar_one()
    assert articles == 1
    assert row.html_content == "<p>today</p>"


async def test_archive_dry_run_changes_nothing(clean_tables):
    session = clean_tables
    old = datetime.now(tz=timezone.utc) - timedelta(days=200)
    await _store(session, _article(1), created_at=old)

    await archive(article_days=90, story_days=365, dry_run=True, vacuum=False,
                  session_factory=_factory(session))

    remaining = (await session.execute(text("SELECT count(*) FROM articles"))).scalar()
    assert remaining == 1
