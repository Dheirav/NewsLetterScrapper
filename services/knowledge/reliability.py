"""
services/knowledge/reliability.py
------------------------------------
Produce a human-readable reliability assessment for a StoryCluster.

Three-factor analysis:
  1. Source tier  — from sources.yaml (Tier 1 sources = premium)
  2. Cross-confirmation — number of distinct sources corroborating the story
  3. Language quality — heuristics for sensationalist language vs. neutral reporting

Output is prose (not a score) so the user builds habit of noticing quality
differences in journalism over time.

Usage:
    from services.knowledge.reliability import assess_reliability
    notes = assess_reliability(cluster, source_tiers)
"""
import logging
import re
from pathlib import Path
from typing import Dict, List

from services.ingestion.source_catalog import load_catalog

log = logging.getLogger(__name__)

from core.schemas.models import StoryCluster

SOURCES_PATH = Path(__file__).parents[2] / "services" / "ingestion" / "sources.yaml"

# Words / phrases associated with sensationalist journalism
_SENSATIONAL_PATTERNS = re.compile(
    r"\b(shocking|outrage|bombshell|explosive|scandal|stunning|unbelievable"
    r"|catastrophic|devastating|terrifying|unthinkable|insane|crazy)\b",
    flags=re.IGNORECASE,
)

# Signals of careful, evidence-based reporting
_NEUTRAL_PATTERNS = re.compile(
    r"(according to|said in a statement|confirmed by|told reporters"
    r"|a spokesperson|in a press release|the data show|research indicates"
    r"|official statement|unnamed official|[A-Z][a-z]+ [A-Z][a-z]+, .{0,30} said)",
    flags=re.IGNORECASE,
)


def _load_source_tiers(path: Path = SOURCES_PATH) -> Dict[str, int]:
    """
    Return {source_name: tier} from sources.yaml.

    Delegates to the shared catalogue so tiers and ranking weights cannot drift
    apart — this module and the personalisation adapter both read the same file
    and previously each parsed it themselves.
    """
    return {name: meta["tier"] for name, meta in load_catalog(path).items()}


def assess_reliability(
    cluster: "StoryCluster",
    source_tiers: Dict[str, int] | None = None,
) -> str:
    """
    Analyse the StoryCluster and return a multi-sentence reliability note.
    """
    if source_tiers is None:
        source_tiers = _load_source_tiers()

    articles = cluster.articles
    sources = list({a.source for a in articles})
    source_count = len(sources)

    # ── Cross-confirmation ───────────────────────────────────────────────────
    if source_count >= 4:
        confirmation = (
            f"This story is confirmed by {source_count} independent sources "
            f"({', '.join(sources[:4])}{'…' if source_count > 4 else ''}), "
            "which gives it high credibility."
        )
    elif source_count == 3:
        confirmation = (
            f"Three sources ({', '.join(sources)}) are covering this story, "
            "providing reasonable cross-confirmation."
        )
    elif source_count == 2:
        confirmation = (
            f"Two outlets ({sources[0]} and {sources[1]}) have reported this independently. "
            "Cross-confirmation is limited — treat with moderate confidence."
        )
    else:
        confirmation = (
            f"Only one source ({sources[0]}) has reported this so far. "
            "No independent cross-confirmation — treat with caution until more outlets pick it up."
        )

    # ── Source tier ──────────────────────────────────────────────────────────
    tiers = [source_tiers.get(s, 3) for s in sources]
    min_tier = min(tiers)
    if min_tier == 1:
        tier_note = "At least one Tier-1 publication (e.g. Reuters, AP, BBC) is among the sources."
    elif min_tier == 2:
        tier_note = "Sources are reputable outlets, though none are in the top tier."
    else:
        tier_note = "Sources are general-interest publications — verify with a primary outlet if possible."

    # ── Language quality ─────────────────────────────────────────────────────
    combined_text = " ".join(a.content[:500] for a in articles)
    sensational_hits = len(_SENSATIONAL_PATTERNS.findall(combined_text))
    neutral_hits = len(_NEUTRAL_PATTERNS.findall(combined_text))

    if sensational_hits >= 3:
        language_note = (
            "⚠ Some articles use emotionally charged language "
            f"({sensational_hits} sensationalist phrases detected). "
            "Read critically and check primary sources directly."
        )
    elif neutral_hits >= 2:
        language_note = (
            "Language across the articles appears measured and evidence-based, "
            "with references to named sources or official statements."
        )
    else:
        language_note = "Language tone is broadly neutral."

    return f"{confirmation} {tier_note} {language_note}"
