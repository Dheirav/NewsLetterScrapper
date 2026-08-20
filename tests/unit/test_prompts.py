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


# ── Output length is the strongest quality lever ─────────────────────────────

def test_combined_prompt_asks_for_substantial_sections():
    """
    Stories came out at ~165 words no matter how much source material was
    supplied, because that is what "2-3 sentences" plus three lots of
    "3-5 sentences" asks for. Output length is capped by the prompt, not by
    num_predict (responses ran ~340 tokens against a 900 budget) and not by the
    article budget. Shortening these numbers silently undoes the measured
    +47% length / +31% concrete-density gain.
    """
    from services.knowledge.prompts import COMBINED_STORY_PROMPT

    assert "4-5 sentences" in COMBINED_STORY_PROMPT, "executive_summary was shortened"
    assert COMBINED_STORY_PROMPT.count("6-8 sentences") == 3, (
        "context, why_it_matters and implications should each ask for 6-8"
    )


def test_combined_prompt_demands_specific_detail():
    """
    Asking for length alone produces padding. The instruction to use the names,
    figures and dates actually present in the sources is what raised concrete
    density rather than diluting it.
    """
    from services.knowledge.prompts import COMBINED_STORY_PROMPT
    lowered = COMBINED_STORY_PROMPT.lower()
    assert "specific names, figures, dates" in lowered
    assert "rather than generalities" in lowered


def test_num_predict_covers_the_requested_length():
    """
    ~250 words plus five talking points does not fit in 900 tokens. When it
    overflows the JSON is cut mid-string and the story is lost entirely.
    """
    import inspect
    from services.knowledge import generator

    src = inspect.getsource(generator._chat_json_sync)
    assert '"num_predict": 2000' in src, "num_predict too low for the current prompt"


def test_malformed_json_is_retried_not_discarded():
    """
    A truncated response used to get zero retries while a dropped connection got
    three, so one bad sample permanently lost the story to the dead-letter
    queue. Measured at 1 in 5 with the longer prompt; 0 in 6 once retried.
    """
    import inspect
    from services.knowledge import generator

    src = inspect.getsource(generator)
    idx = src.index("def _chat_json_sync")
    decorator = src[max(0, idx - 800):idx]
    assert "JSONDecodeError" in decorator
