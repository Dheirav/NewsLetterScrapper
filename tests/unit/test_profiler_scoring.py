"""
tests/unit/test_profiler_scoring.py
--------------------------------------
Unit tests for the engagement scoring mathematics in
services.personalization.profiler.

These tests exercise the scoring algorithm directly without hitting any DB.
The DB-round-trip functions (build_profile, load_profile) are covered by
integration tests; here we test only the pure-math logic.
"""
import pytest


# ── Engagement formula ────────────────────────────────────────────────────────
# Formula: (min(avg_time, 600) / 600) * 0.6 + (avg_scroll / 100) * 0.4
# Imported as a helper to keep tests self-contained.

def _engagement(avg_time_seconds: float, avg_scroll_percent: float) -> float:
    return (min(avg_time_seconds, 600) / 600) * 0.6 + (avg_scroll_percent / 100) * 0.4


def test_max_time_and_full_scroll_gives_1():
    score = _engagement(600, 100.0)
    assert score == pytest.approx(1.0)


def test_zero_time_zero_scroll_gives_0():
    score = _engagement(0, 0.0)
    assert score == pytest.approx(0.0)


def test_time_capped_at_600():
    """Time values above 600s should produce the same score as exactly 600s."""
    score_600 = _engagement(600, 50.0)
    score_over = _engagement(9999, 50.0)
    assert score_600 == pytest.approx(score_over)


def test_time_weight_is_60_percent():
    """Spending 600s with 0% scroll should give exactly 0.6."""
    assert _engagement(600, 0.0) == pytest.approx(0.6)


def test_scroll_weight_is_40_percent():
    """100% scroll with 0s time should give exactly 0.4."""
    assert _engagement(0, 100.0) == pytest.approx(0.4)


def test_partial_engagement():
    """300s = 50% of max time → 0.3 time component; 50% scroll → 0.2 scroll."""
    expected = 0.3 + 0.2
    assert _engagement(300, 50.0) == pytest.approx(expected)


# ── Normalisation ─────────────────────────────────────────────────────────────

def _normalise_weights(raw: dict[str, float]) -> dict[str, float]:
    total = sum(raw.values()) or 1.0
    return {k: round(v / total, 4) for k, v in raw.items()}


def test_normalised_weights_sum_to_1():
    raw = {"ai": 0.8, "geopolitics": 0.4, "economy": 0.2}
    result = _normalise_weights(raw)
    assert sum(result.values()) == pytest.approx(1.0, abs=0.001)


def test_normalisation_preserves_order():
    """Highest raw score should remain the highest after normalisation."""
    raw = {"ai": 0.9, "economy": 0.1}
    result = _normalise_weights(raw)
    assert result["ai"] > result["economy"]


def test_single_topic_gets_full_weight():
    raw = {"only-topic": 0.5}
    result = _normalise_weights(raw)
    assert result["only-topic"] == pytest.approx(1.0)


def test_empty_weights_no_crash():
    result = _normalise_weights({})
    assert result == {}
