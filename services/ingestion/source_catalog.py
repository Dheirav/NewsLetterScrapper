"""
services/ingestion/source_catalog.py
--------------------------------------
Single loader for the source metadata in ``sources.yaml``.

``reliability.py`` already parsed this file for tiers and the adapter now needs
weights, so the parsing lives here once rather than in each consumer.

Fields per source:
    name         display name, also the join key used on Article.source
    tier         1 = elite, 2 = good, 3 = general
    source_type  "news" | "analysis" | "research"
    weight       ranking influence, news t1=1.0 down to research t2=0.3
"""
import logging
from pathlib import Path
from typing import Dict

import yaml

log = logging.getLogger(__name__)

SOURCES_PATH = Path(__file__).parent / "sources.yaml"

# Unknown sources rank as ordinary news rather than being penalised: an entry
# missing from the catalogue is a bookkeeping gap, not a quality signal.
DEFAULT_WEIGHT = 1.0
DEFAULT_TIER = 3

_CACHE: Dict[str, dict] | None = None


def load_catalog(path: Path = SOURCES_PATH) -> Dict[str, dict]:
    """Return {source_name: {tier, weight, source_type}}. Cached after first read."""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
        _CACHE = {
            s["name"]: {
                "tier": s.get("tier", DEFAULT_TIER),
                "weight": float(s.get("weight", DEFAULT_WEIGHT)),
                "source_type": s.get("source_type", "news"),
                # Carried so feed-health checks can probe a source without
                # parsing sources.yaml a second time.
                "url": s.get("url", ""),
                "domain": s.get("domain", ""),
            }
            for s in data.get("sources", [])
        }
    except Exception as exc:
        log.warning("Failed to load source catalogue from %s: %s", path, exc)
        _CACHE = {}
    return _CACHE


def source_weight(name: str) -> float:
    return load_catalog().get(name, {}).get("weight", DEFAULT_WEIGHT)


def source_tier(name: str) -> int:
    return load_catalog().get(name, {}).get("tier", DEFAULT_TIER)


def mean_source_weight(names: list[str]) -> float:
    """
    Mean ranking weight across the sources backing a story.

    Averaging rather than taking the maximum is deliberate: a story carried by
    one wire report and three research blogs should rank below one carried by
    four wire reports, which is the whole point of weighting by source type.
    """
    if not names:
        return DEFAULT_WEIGHT
    return sum(source_weight(n) for n in names) / len(names)
