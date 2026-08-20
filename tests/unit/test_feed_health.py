"""
Tests for feed-health reporting and the source catalogue's integrity.

The gap this closes: bad feeds are skipped by design — "failures degrade, they
do not raise" — so a source that stops returning entries vanishes from the
briefing with no error anywhere. Reuters and Associated Press, the two
highest-weighted tier-1 wires and the ones reliability.py name-checks in its
output, had both been dead for an unknown period before anyone noticed.
"""
import pytest
import yaml

from scripts.feed_health import Health
from services.ingestion.source_catalog import load_catalog


# ── Status classification ────────────────────────────────────────────────────

def test_no_entries_is_silent():
    assert Health("X", 1, 1.0, entries=0).status == "SILENT"


def test_an_error_is_silent_even_with_entries():
    assert Health("X", 1, 1.0, entries=5, error="ConnectError").status == "SILENT"


def test_entries_that_never_scrape_are_unusable():
    """A paywall: the feed works, the articles do not."""
    h = Health("X", 1, 1.0, entries=40, sampled=40, scraped_ok=0)
    assert h.status == "UNUSABLE"


def test_partial_scraping_still_counts_as_ok():
    """Some articles failing is normal; none succeeding is the signal."""
    h = Health("X", 1, 1.0, entries=40, sampled=40, scraped_ok=3)
    assert h.status == "OK"


def test_silent_and_unusable_are_distinguished():
    """
    They need different fixes — a dead URL versus a paywall — and collapsing
    them into "broken" is how "it returns 40 entries" hides that none of them
    are readable.
    """
    dead = Health("A", 1, 1.0, entries=0)
    walled = Health("B", 1, 1.0, entries=40, sampled=40, scraped_ok=0)
    assert dead.status != walled.status


# ── The catalogue must support probing ───────────────────────────────────────

def test_catalogue_exposes_the_url():
    """feed_health probes sources through the catalogue, not by re-parsing YAML."""
    for name, meta in load_catalog().items():
        assert meta.get("url", "").startswith("http"), f"{name} has no usable url"


def test_every_source_has_the_fields_ranking_depends_on():
    for name, meta in load_catalog().items():
        assert meta["tier"] in (1, 2, 3), name
        assert 0.0 < meta["weight"] <= 1.0, name
        assert meta["source_type"] in ("news", "analysis", "research"), name


# ── The sources that were removed must stay removed ──────────────────────────

RETIRED = {
    "Reuters": "feeds.reuters.com no longer resolves",
    "Associated Press": "rsshub proxy returns 403",
    "Brookings": "HTTP 200 with zero entries",
    "The Athletic": "nytimes.com/athletic/rss 404s",
    "The Batch (deeplearning.ai)": "deeplearning.ai feed 404s",
    "The Economist": "0 of 40 articles scrapeable",
    "Financial Times": "0 of 18 articles scrapeable",
    "Ars Technica": "0 of 40 articles scrapeable",
    "NDTV India": "0 of 47 articles scrapeable",
}


@pytest.mark.parametrize("name,reason", RETIRED.items())
def test_dead_and_paywalled_sources_are_not_reintroduced(name, reason):
    assert name not in load_catalog(), f"{name} was removed: {reason}"


def test_no_duplicate_source_names():
    """Names are the join key onto Article.source; a duplicate silently merges two feeds."""
    raw = yaml.safe_load(open("services/ingestion/sources.yaml"))["sources"]
    names = [s["name"] for s in raw]
    assert len(names) == len(set(names))


def test_every_domain_is_renderable():
    from services.newsletter._domains import SECTION_ORDER
    for name, meta in load_catalog().items():
        assert meta["domain"] in SECTION_ORDER, f"{name} -> {meta['domain']}"


def test_each_domain_keeps_more_than_one_source():
    """
    Cross-confirmation needs at least two outlets per domain, and the removals
    took four sources out of Economy and Technology.
    """
    from collections import Counter
    counts = Counter(m["domain"] for m in load_catalog().values())
    thin = {d: n for d, n in counts.items() if n < 2}
    assert not thin, f"domains with a single source cannot cross-confirm: {thin}"
