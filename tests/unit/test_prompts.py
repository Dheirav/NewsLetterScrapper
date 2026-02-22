"""
tests/unit/test_prompts.py
---------------------------
Unit tests for services.knowledge.prompts.build_articles_text.
"""
import pytest
from datetime import datetime, timezone

from core.schemas.models import Article
from services.knowledge.prompts import build_articles_text


def _make_article(title: str, content: str, source: str = "TestSource") -> Article:
    return Article(
        title=title,
        source=source,
        url=f"https://example.com/{title[:10]}",
        published_at=datetime.now(tz=timezone.utc),
        content=content,
    )


def test_orders_by_content_length_descending():
    """Richest articles (most content) should appear first."""
    short = _make_article("Short", "x" * 100)
    long  = _make_article("Long",  "x" * 900)
    medium = _make_article("Medium", "x" * 400)

    text = build_articles_text([short, long, medium])
    # Header format: "[Article 1 — TestSource]\nTitle: Long\n..."
    first_title_line = text.split("\n")[1]   # second line is "Title: <name>"
    assert "Long" in first_title_line


def test_truncates_at_max_chars_per_article():
    """Content beyond max_chars_per_article must be cut off."""
    article = _make_article("Big Article", "Z" * 2000)  # use Z to avoid overlap with title
    text = build_articles_text([article], max_chars_per_article=500)
    # Content body starts after the title line
    body = text.split("Big Article\n", 1)[1]
    assert len(body) <= 500


def test_respects_max_articles_cap():
    """Only the top max_articles articles should appear in the output."""
    articles = [_make_article(f"Article {i}", "x" * (100 * i)) for i in range(1, 8)]
    text = build_articles_text(articles, max_articles=3)
    # Count how many [Article N — ...] headers appear
    count = text.count("[Article ")
    assert count == 3


def test_empty_content_handled():
    """Articles with no content should not raise errors."""
    article = _make_article("No Content", "")
    text = build_articles_text([article])
    assert "No Content" in text


def test_output_includes_source_name():
    """Each block should show the source name."""
    article = _make_article("Title", "Body text", source="Reuters")
    text = build_articles_text([article])
    assert "Reuters" in text


def test_empty_articles_list():
    assert build_articles_text([]) == ""
