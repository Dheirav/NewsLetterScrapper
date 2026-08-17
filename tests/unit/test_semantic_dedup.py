"""
Tests for cross-day semantic deduplication.

The gap this closes: URL dedup compares against the database, TF-IDF title
dedup compares only within the current batch. A story republished the next day
under a new URL passed both, was embedded again, and could form a second
cluster for an event already briefed.

The threshold matters more than it looks. Measured on 13,836 real articles,
republished and syndicated copies score 0.99–1.00, while two independent
outlets covering the same event score around 0.96. The second group must
survive: multi-source coverage is what clustering looks for and what the
cross-confirmation grading in knowledge/reliability.py counts. Collapsing it
would make stories appear less corroborated than they are.
"""
import pytest

from core.config import Settings
from core.schemas.models import Article
from services.ingestion.semantic_dedup import mark_semantic_duplicates


def _article(aid, title="T", source="Reuters", embedding=(0.1,)):
    return Article(
        id=aid, title=title, source=source, url=f"https://example.com/{aid}",
        published_at=None, content="body",
        embedding=list(embedding) if embedding is not None else None,
    )


class StubSession:
    """Returns a scripted match set, records the statements it was given."""

    def __init__(self, matches=()):
        self.matches = list(matches)
        self.executed = []

    async def execute(self, stmt, params=None):
        self.executed.append((str(stmt), params))
        rows = self.matches if "LATERAL" in str(stmt) else []
        # The UPDATE returns nothing meaningful.
        class R:
            def fetchall(self_inner):
                return rows
        return R()

    async def flush(self):
        pass


# ── Threshold policy ─────────────────────────────────────────────────────────

def test_default_threshold_separates_republication_from_coverage():
    """
    Anchored to real measurements: 0.9832 was a recurring column whose content
    differs weekly, 0.9647 and 0.9642 were genuine two-outlet coverage.
    """
    t = Settings().dedup_semantic_threshold
    assert t > 0.9832, "must not collapse a recurring column into its own past editions"
    assert t > 0.965, "must not collapse independent outlets covering one event"
    assert t <= 0.99, "must still catch retitled republication (measured at 0.9930)"


def test_lookback_window_default():
    assert Settings().dedup_lookback_days == 7


# ── Behaviour ────────────────────────────────────────────────────────────────

async def test_duplicates_are_removed_from_the_returned_batch():
    articles = [_article(1), _article(2), _article(3)]
    session = StubSession(matches=[(2, 1, "Original", "Reuters", 0.997)])

    fresh = await mark_semantic_duplicates(articles, session)

    assert [a.id for a in fresh] == [1, 3]


async def test_duplicates_are_marked_not_deleted():
    """
    The row must remain so URL dedup keeps recognising it. Deleting would let
    the same URL be re-fetched, re-scraped and re-embedded every single run.
    """
    session = StubSession(matches=[(2, 1, "Original", "Reuters", 0.997)])

    await mark_semantic_duplicates([_article(1), _article(2)], session)

    statements = [sql for sql, _ in session.executed]
    assert any("UPDATE articles SET duplicate_of" in s for s in statements)
    assert not any("DELETE" in s.upper() for s in statements)


async def test_nothing_marked_when_no_match_clears_the_threshold():
    articles = [_article(1), _article(2)]
    session = StubSession(matches=[])

    fresh = await mark_semantic_duplicates(articles, session)

    assert [a.id for a in fresh] == [1, 2]
    assert not any("UPDATE" in sql for sql, _ in session.executed)


async def test_threshold_and_window_are_passed_to_the_query():
    session = StubSession(matches=[])
    await mark_semantic_duplicates(
        [_article(1)], session, threshold=0.93, lookback_days=21
    )
    _, params = session.executed[0]
    assert params["threshold"] == 0.93
    assert params["days"] == 21


async def test_settings_supply_the_defaults():
    from core.config import settings
    session = StubSession(matches=[])
    await mark_semantic_duplicates([_article(1)], session)
    _, params = session.executed[0]
    assert params["threshold"] == settings.dedup_semantic_threshold
    assert params["days"] == settings.dedup_lookback_days


# ── Pass-through cases ───────────────────────────────────────────────────────

async def test_unembedded_articles_pass_through_untouched():
    """Nothing to compare, so they must not be dropped."""
    articles = [_article(1, embedding=None), _article(2, embedding=None)]
    session = StubSession(matches=[])

    fresh = await mark_semantic_duplicates(articles, session)

    assert [a.id for a in fresh] == [1, 2]
    assert session.executed == [], "no query should run with no candidates"


async def test_articles_without_a_db_id_pass_through():
    unsaved = _article(None)
    session = StubSession(matches=[])

    fresh = await mark_semantic_duplicates([unsaved], session)

    assert fresh == [unsaved]
    assert session.executed == []


async def test_empty_batch_is_a_no_op():
    session = StubSession()
    assert await mark_semantic_duplicates([], session) == []
    assert session.executed == []


async def test_unembedded_articles_survive_alongside_duplicates():
    """A mixed batch must only lose the confirmed duplicates."""
    articles = [_article(1), _article(2), _article(3, embedding=None)]
    session = StubSession(matches=[(2, 1, "Original", "Reuters", 0.99)])

    fresh = await mark_semantic_duplicates(articles, session)

    assert [a.id for a in fresh] == [1, 3]


# ── Query shape ──────────────────────────────────────────────────────────────

async def test_only_earlier_articles_can_be_the_original():
    """
    Without an ordering constraint a story could be recorded as a duplicate of
    one published after it, and two articles in the same batch could each be
    marked against the other.
    """
    session = StubSession(matches=[])
    await mark_semantic_duplicates([_article(1)], session)
    sql, _ = session.executed[0]
    assert "b.created_at < c.created_at" in sql
    assert "b.id < c.id" in sql, "ties on timestamp need a total order"


async def test_already_marked_duplicates_are_not_chained_against():
    session = StubSession(matches=[])
    await mark_semantic_duplicates([_article(1)], session)
    sql, _ = session.executed[0]
    assert "b.duplicate_of IS NULL" in sql


async def test_the_batch_resolves_in_a_single_query():
    """
    One LATERAL join rather than a lookup per article: 445 real articles
    complete in ~14s this way, versus not completing at all as a loop.
    """
    articles = [_article(i) for i in range(1, 51)]
    session = StubSession(matches=[])

    await mark_semantic_duplicates(articles, session)

    selects = [sql for sql, _ in session.executed if "LATERAL" in sql]
    assert len(selects) == 1, f"expected 1 lookup for 50 articles, got {len(selects)}"
