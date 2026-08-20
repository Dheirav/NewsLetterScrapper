"""
Tests for the coherence gate on knowledge generation.

A cluster can be large and still not be a story. Measured on real data, the
least coherent cluster of the day held 13 articles about unrelated Adam Driver
projects and scored 0.683, while a 3-article cluster about a specific drone
lease scored 0.862. Because clusters are ranked by article count, the bucket
would have been near the top of the briefing — so the gate has to run BEFORE
the cap, not after.
"""
import pytest

from core.config import Settings
from core.schemas.models import Article, StoryCluster
from services.knowledge.generator import _filter_incoherent, cluster_coherence

DIM = 768


def _vec(i):
    v = [0.0] * DIM
    v[i % DIM] = 1.0
    return v


def _article(vec):
    return Article(title="t", source="Reuters", url="u", published_at=None,
                   content="body", embedding=vec)


def _cluster(label, vecs):
    return StoryCluster(label, label, [_article(v) for v in vecs], None)


# ── The metric ───────────────────────────────────────────────────────────────

def test_identical_articles_score_one():
    assert cluster_coherence(_cluster("event", [_vec(0)] * 4)) == pytest.approx(1.0)


def test_unrelated_articles_score_zero():
    c = _cluster("bucket", [_vec(0), _vec(100), _vec(200), _vec(300)])
    assert cluster_coherence(c) == pytest.approx(0.0)


def test_a_real_event_outscores_a_topic_bucket():
    event = _cluster("event", [_vec(0), _vec(0), _vec(0)])
    bucket = _cluster("bucket", [_vec(0), _vec(50), _vec(100)])
    assert cluster_coherence(event) > cluster_coherence(bucket)


def test_single_article_is_trivially_coherent():
    """Singletons are excluded earlier by min_cluster_articles."""
    assert cluster_coherence(_cluster("one", [_vec(0)])) == 1.0


def test_missing_embeddings_do_not_suppress_a_story():
    """
    A missing vector is a bookkeeping gap, not evidence of incoherence.
    Returning 0.0 here would silently delete stories whenever embedding failed.
    """
    c = StoryCluster("x", "x", [_article(None), _article(None)], None)
    assert cluster_coherence(c) == 1.0


def test_partial_embeddings_use_what_exists():
    c = StoryCluster("x", "x", [_article(_vec(0)), _article(_vec(0)), _article(None)], None)
    assert cluster_coherence(c) == pytest.approx(1.0)


# ── The gate ─────────────────────────────────────────────────────────────────

def test_incoherent_clusters_are_dropped():
    good = _cluster("real event", [_vec(0), _vec(0), _vec(0)])
    bad = _cluster("topic bucket", [_vec(0), _vec(100), _vec(200)])

    kept = _filter_incoherent([good, bad], threshold=0.70)

    assert [c.topic_label for c in kept] == ["real event"]


def test_a_large_incoherent_cluster_is_still_dropped():
    """
    The observed failure: 13 articles, coherence 0.683, and top of the list
    under count-based ranking.
    """
    big_bucket = _cluster("13 unrelated films", [_vec(i * 7) for i in range(13)])
    small_event = _cluster("one real event", [_vec(0), _vec(0)])

    kept = _filter_incoherent([big_bucket, small_event], threshold=0.70)

    assert [c.topic_label for c in kept] == ["one real event"]


def test_nothing_is_dropped_when_everything_is_coherent():
    clusters = [_cluster(f"e{i}", [_vec(i), _vec(i)]) for i in range(4)]
    assert len(_filter_incoherent(clusters, threshold=0.70)) == 4


def test_empty_input_is_safe():
    assert _filter_incoherent([], threshold=0.70) == []


def test_threshold_is_inclusive():
    """A cluster exactly at the threshold is kept, not discarded."""
    c = _cluster("borderline", [_vec(0), _vec(0)])   # scores 1.0
    assert _filter_incoherent([c], threshold=1.0) == [c]


# ── Configuration ────────────────────────────────────────────────────────────

def test_default_threshold_is_conservative():
    """
    Measured across 44 real clusters: 0.70 drops 3, 0.75 drops 24, 0.80 drops
    37. The default must sit at the conservative end — one day's distribution
    is not enough evidence to discard half the briefing.
    """
    t = Settings().min_cluster_coherence
    assert 0.60 <= t <= 0.72, f"{t} is too aggressive for a single-day sample"


def test_context_window_exceeds_the_configured_prompt_budget():
    """
    num_ctx must cover the source material, or the prompt is silently truncated
    on the input side and the story is written from less than it appears.
    """
    s = Settings()
    approx_prompt_tokens = (s.knowledge_max_articles * s.knowledge_chars_per_article) // 4
    assert s.ollama_num_ctx > approx_prompt_tokens + 900 + 400, (
        "num_ctx leaves no room for the prompt plus num_predict"
    )
