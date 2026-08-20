"""
Tests for source-weighted story ranking.

sources.yaml has carried `source_type` and `weight` on every entry since
migration 007, and ingestion stored them on every article — but nothing read
them, so the stated goal ("control their influence during story ranking") was
never actually implemented.

Weighting is multiplicative in `adapter._score`: the weight expresses how much
influence a kind of outlet should have, which is a proportion of the score
rather than a fixed offset that would swamp small personal weights.
"""
import pytest

from core.schemas.models import KnowledgeStory, UserProfile
from core.utils import slugify
from services.ingestion.source_catalog import (
    load_catalog,
    mean_source_weight,
    source_tier,
    source_weight,
)
from services.personalization.adapter import adapt_newsletter


def _story(label, sources, source_count=None):
    return KnowledgeStory(
        cluster_id=f"c-{slugify(label)}", topic_label=label,
        executive_summary="S.", context="C.", why_it_matters="M.",
        implications="I.", talking_points=["a", "b", "c", "d", "e"],
        source_count=source_count if source_count is not None else len(sources),
        article_urls=[f"https://example.com/{i}" for i in range(len(sources))],
        article_sources=list(sources),
    )


# ── Catalogue ────────────────────────────────────────────────────────────────

def test_catalogue_loads_every_source():
    """
    Counted against the YAML rather than a literal. Sources legitimately come
    and go — nine were retired at once when their feeds died or paywalled — and
    a hardcoded count turns routine catalogue maintenance into a test failure.
    """
    import yaml
    raw = yaml.safe_load(open("services/ingestion/sources.yaml"))["sources"]
    assert len(load_catalog()) == len(raw)
    assert len(raw) > 20, "catalogue looks truncated"


# sources.yaml documents this table:
#   source_type | tier 1 | tier 2
#   news        |  1.0   |  0.9
#   analysis    |  0.7   |  0.6
#   research    |  0.4   |  0.3
EXPECTED_WEIGHTS = {
    ("news", 1): 1.0, ("news", 2): 0.9,
    ("analysis", 1): 0.7, ("analysis", 2): 0.6,
    ("research", 1): 0.4, ("research", 2): 0.3,
}


def test_weights_follow_the_documented_tier_table():
    """
    Checks the rule against every source rather than naming three outlets. The
    original named Reuters and Brookings, both since retired — so it failed for
    catalogue maintenance rather than for a real drift in the weighting.
    """
    wrong = []
    for name, meta in load_catalog().items():
        key = (meta["source_type"], meta["tier"])
        expected = EXPECTED_WEIGHTS.get(key)
        if expected is not None and meta["weight"] != expected:
            wrong.append(f"{name}: {meta['source_type']} tier {meta['tier']} "
                         f"is {meta['weight']}, table says {expected}")
    assert not wrong, "weights drifted from the documented table:\n  " + "\n  ".join(wrong)


def test_the_three_source_types_are_ordered_by_influence():
    """news outranks analysis outranks research at equal tier — the point of the weights."""
    assert EXPECTED_WEIGHTS[("news", 1)] > EXPECTED_WEIGHTS[("analysis", 1)]
    assert EXPECTED_WEIGHTS[("analysis", 1)] > EXPECTED_WEIGHTS[("research", 1)]


def test_unknown_source_is_not_penalised():
    """A gap in the catalogue is bookkeeping, not a quality signal."""
    assert source_weight("Some New Outlet") == 1.0
    assert source_tier("Some New Outlet") == 3


def test_mean_weight_averages_rather_than_taking_the_best():
    """
    One wire report plus three research blogs should rank below four wire
    reports — taking the max would erase the distinction entirely.
    """
    mixed = mean_source_weight(["Reuters", "DeepMind Blog"])
    assert mixed == pytest.approx(0.7)
    assert mixed < mean_source_weight(["Reuters", "BBC News"])


def test_mean_of_no_sources_is_neutral():
    assert mean_source_weight([]) == 1.0


# ── Ranking ──────────────────────────────────────────────────────────────────

def test_news_outranks_research_at_equal_engagement():
    profile = UserProfile(topic_weights={
        slugify("Wire story"): 0.5,
        slugify("Research story"): 0.5,
    })
    stories = [
        _story("Research story", ["DeepMind Blog"]),
        _story("Wire story", ["Reuters"]),
    ]

    ordered = adapt_newsletter(stories, profile)

    assert ordered[0].topic_label == "Wire story", (
        "equal engagement should be broken by source weight"
    )


def test_strong_engagement_still_beats_a_heavier_source():
    """Weighting tilts the ranking; it must not override the reader entirely."""
    profile = UserProfile(topic_weights={
        slugify("Loved research"): 0.9,
        slugify("Ignored wire"): 0.05,
    })
    stories = [
        _story("Ignored wire", ["Reuters"]),
        _story("Loved research", ["DeepMind Blog"]),
    ]

    ordered = adapt_newsletter(stories, profile)

    assert ordered[0].topic_label == "Loved research"


def test_cross_confirmation_bonus_is_also_weighted():
    """
    The multi-source bonus and the personal weight are both proportions of the
    same score, so the source weight scales the whole thing.
    """
    profile = UserProfile(topic_weights={slugify("Anything else"): 1.0})
    heavy = _story("Wire cluster", ["Reuters", "BBC News", "Associated Press"])
    light = _story("Research cluster", ["DeepMind Blog", "The Batch (deeplearning.ai)",
                                        "Nature News"], source_count=3)

    ordered = adapt_newsletter([light, heavy], profile)

    assert ordered[0].topic_label == "Wire cluster"


def test_ranking_is_unchanged_when_there_is_no_profile():
    """With no reading history the adapter still returns stories untouched."""
    stories = [_story("A", ["DeepMind Blog"]), _story("B", ["Reuters"])]
    ordered = adapt_newsletter(stories, UserProfile())
    assert [s.topic_label for s in ordered] == ["A", "B"]


def test_unknown_sources_do_not_change_relative_order():
    profile = UserProfile(topic_weights={
        slugify("First"): 0.6, slugify("Second"): 0.3,
    })
    stories = [
        _story("Second", ["Unknown Outlet"]),
        _story("First", ["Another Unknown"]),
    ]
    ordered = adapt_newsletter(stories, profile)
    assert [s.topic_label for s in ordered] == ["First", "Second"]


# ── One loader, not two ──────────────────────────────────────────────────────

def test_reliability_reads_the_same_catalogue():
    """
    reliability.py parsed sources.yaml itself for tiers while the adapter needed
    weights from the same file. Both now go through source_catalog.
    """
    from services.knowledge.reliability import _load_source_tiers

    tiers = _load_source_tiers()
    assert len(tiers) == len(load_catalog())
    # Compare on whatever the catalogue actually holds, not a named outlet.
    for name in list(load_catalog())[:5]:
        assert tiers[name] == source_tier(name)
