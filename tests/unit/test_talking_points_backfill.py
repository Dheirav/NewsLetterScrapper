"""
Tests for talking-point recovery in concise mode.

Measured on a real run: 5 of 30 stories came back with a single talking point
against a prompt asking for five, while the four prose sections in the same
response were fine. Models collapse arrays in JSON mode more readily than they
truncate prose, so the fix re-requests just that field with detailed mode's
focused prompt rather than discarding four good sections for a full retry.
"""
import pytest

from core.schemas.models import Article, StoryCluster
from services.knowledge import generator
from services.knowledge.generator import MIN_TALKING_POINTS, _backfill_talking_points


def _cluster():
    article = Article(
        title="US and China resume tariff talks", source="Reuters",
        url="https://example.com/1", published_at=None, content="Body. " * 40,
    )
    return StoryCluster("c1", "US-China tariff talks", [article], None)


@pytest.fixture
def fake_llm(monkeypatch):
    """Capture the prompt and return a scripted numbered list."""
    calls = []

    def _make(response):
        def _fake(prompt):
            calls.append(prompt)
            return response
        monkeypatch.setattr(generator, "_chat_text_sync", _fake)
        return calls

    return _make


def test_threshold_matches_the_adapter_trim():
    """
    The adapter trims low-engagement stories to three points, so three is the
    floor at which the section still reads as designed.
    """
    from services.personalization.adapter import LOW_ENGAGEMENT_TALKING_POINTS
    assert MIN_TALKING_POINTS == LOW_ENGAGEMENT_TALKING_POINTS


async def test_backfill_recovers_a_collapsed_list(fake_llm):
    fake_llm("1. First point\n2. Second point\n3. Third point\n4. Fourth\n5. Fifth")
    result = await _backfill_talking_points(_cluster(), ["only one"])
    assert len(result) == 5
    assert result[0] == "First point"


async def test_backfill_uses_the_focused_prompt(fake_llm):
    calls = fake_llm("1. a\n2. b\n3. c")
    await _backfill_talking_points(_cluster(), ["one"])
    assert len(calls) == 1
    assert "TALKING POINTS" in calls[0]
    assert "US-China tariff talks" in calls[0]


async def test_backfill_keeps_the_original_when_it_does_no_better(fake_llm):
    """A second collapse must not make the story worse."""
    fake_llm("1. single")
    original = ["already here", "and here"]
    assert await _backfill_talking_points(_cluster(), original) == original


async def test_backfill_failure_is_not_fatal(monkeypatch):
    """
    Generation already succeeded; losing the extra call must not discard the
    four good sections that came with it.
    """
    def _boom(_prompt):
        raise ConnectionError("ollama unreachable")

    monkeypatch.setattr(generator, "_chat_text_sync", _boom)
    original = ["one"]
    assert await _backfill_talking_points(_cluster(), original) == original


@pytest.mark.parametrize("existing,should_call", [
    ([], True),
    (["one"], True),
    (["one", "two"], True),
    (["one", "two", "three"], False),
    (["one", "two", "three", "four", "five"], False),
])
def test_only_deficient_stories_pay_for_a_second_call(existing, should_call):
    """
    The extra call costs ~20s of local inference, so it must fire only below
    the threshold — not on every story.
    """
    assert (len(existing) < MIN_TALKING_POINTS) is should_call
