"""
tests/unit/test_deduplicator.py
---------------------------------
Unit tests for services.ingestion.deduplicator.

No real DB needed — the AsyncSession is mocked so these tests run
completely offline without Postgres.
"""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from services.ingestion.deduplicator import _normalise, _tfidf_dedup, deduplicate
from core.schemas.models import Article


def _make_article(title: str, url: str, content: str = "some content") -> Article:
    return Article(
        title=title,
        source="TestSource",
        url=url,
        published_at=datetime.now(tz=timezone.utc),
        content=content,
    )


# ── _normalise ────────────────────────────────────────────────────────────────

def test_normalise_lowercases():
    assert _normalise("Hello World") == "hello world"


def test_normalise_strips_punctuation():
    assert _normalise("It's a test!") == "its a test"


def test_normalise_collapses_whitespace():
    assert _normalise("lots   of   spaces") == "lots of spaces"


# ── _tfidf_dedup (replaces the old O(n²) _similarity approach) ───────────────

def test_tfidf_dedup_keeps_unique_articles():
    articles = [
        _make_article("US Imposes New Tariffs on China", "https://a.com/1"),
        _make_article("EU Central Bank Raises Rates",   "https://b.com/2"),
        _make_article("NASA Moon Mission Delayed",       "https://c.com/3"),
    ]
    result = _tfidf_dedup(articles, threshold=0.85)
    assert len(result) == 3


def test_tfidf_dedup_removes_near_identical_titles():
    articles = [
        _make_article("Federal Reserve raises interest rates decisively", "https://a.com/1"),
        _make_article("Federal Reserve raises interest rates decisively", "https://b.com/2"),
    ]
    result = _tfidf_dedup(articles, threshold=0.85)
    assert len(result) == 1


def test_tfidf_dedup_single_article_passthrough():
    articles = [_make_article("Only one article", "https://a.com/1")]
    result = _tfidf_dedup(articles, threshold=0.85)
    assert len(result) == 1


def test_tfidf_dedup_empty_input():
    assert _tfidf_dedup([], threshold=0.85) == []


# ── deduplicate ───────────────────────────────────────────────────────────────

@pytest.fixture
def mock_session():
    """Return a minimal mock AsyncSession that reports no existing URLs."""
    session = AsyncMock()
    result = MagicMock()
    result.fetchall.return_value = []          # no existing URLs in DB
    session.execute = AsyncMock(return_value=result)
    return session


@pytest.mark.asyncio
async def test_deduplicate_keeps_unique_articles(mock_session):
    articles = [
        _make_article("US Imposes New Tariffs on China", "https://a.com/1"),
        _make_article("EU Central Bank Raises Rates",   "https://b.com/2"),
        _make_article("NASA Moon Mission Delayed",       "https://c.com/3"),
    ]
    result = await deduplicate(articles, mock_session)
    assert len(result) == 3


@pytest.mark.asyncio
async def test_deduplicate_removes_near_duplicate_titles(mock_session):
    articles = [
        _make_article("Federal Reserve raises interest rates", "https://a.com/1"),
        _make_article("Federal Reserve raises interest rates sharply", "https://b.com/2"),
    ]
    # Second title is very similar — should be dropped
    result = await deduplicate(articles, mock_session, threshold=0.85)
    assert len(result) == 1
    assert result[0].url == "https://a.com/1"


@pytest.mark.asyncio
async def test_deduplicate_removes_exact_title_duplicate(mock_session):
    articles = [
        _make_article("Climate Summit Opens in Dubai", "https://a.com/1"),
        _make_article("Climate Summit Opens in Dubai", "https://b.com/2"),
    ]
    result = await deduplicate(articles, mock_session)
    assert len(result) == 1


@pytest.mark.asyncio
async def test_deduplicate_removes_db_url_matches(mock_session):
    """Articles whose URLs already exist in the DB must be dropped."""
    existing_url = "https://already-in-db.com/article"
    mock_session.execute.return_value.fetchall.return_value = [(existing_url,)]

    articles = [
        _make_article("Some fresh article", "https://fresh.com/1"),
        _make_article("Old article already stored", existing_url),
    ]
    result = await deduplicate(articles, mock_session)
    assert len(result) == 1
    assert result[0].url == "https://fresh.com/1"


@pytest.mark.asyncio
async def test_deduplicate_empty_input(mock_session):
    result = await deduplicate([], mock_session)
    assert result == []
