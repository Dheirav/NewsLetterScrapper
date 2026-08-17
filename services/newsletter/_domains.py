"""
services/newsletter/_domains.py
---------------------------------
Single source of truth for domain keyword mapping.
Used by both assembler.py (for logging) and renderer.py (for Jinja2 filter).
"""
import re
from typing import Dict, List, Pattern

# SECTION_ORDER controls the sequence of sections in the rendered newsletter.
SECTION_ORDER = ["World", "India", "Policy", "Economy", "AI", "Technology", "Science", "Health", "Sport", "Entertainment", "Other"]

# Keyword lists for domain inference from topic_label text.
# Precedence: first match wins, in dict iteration order.
#
# Keywords are matched on WORD BOUNDARIES, not as raw substrings. This matters:
# a plain `"rate" in label` also fires on "corporate", "separate" and "moderate",
# and `"app"` fires on "happened" — which silently filed general news under
# Economy and Technology. Keep entries as whole words or whole phrases; there is
# no need to pad them with spaces (the old " ai " hack).
DOMAIN_KEYWORDS: Dict[str, List[str]] = {
    "AI": [
        "artificial intelligence", "ai", "llm", "large language model",
        "machine learning", "deep learning", "neural network", "openai",
        "deepmind", "anthropic", "gemini", "gpt", "ollama", "chatbot",
    ],
    "Technology": [
        "tech", "software", "hardware", "data", "cyber", "internet",
        "chip", "semiconductor", "computer", "robot", "space", "satellite",
        "quantum", "startup", "app", "platform",
    ],
    "Economy": [
        "market", "trade", "tariff", "bank", "gdp", "inflation", "economy",
        "finance", "stock", "recession", "currency", "imf", "world bank",
        "rate", "bonds", "debt", "fiscal", "monetary",
    ],
    "Science": [
        "research", "study", "climate", "discovery", "science",
        "vaccine", "biology", "physics", "chemistry", "astronomy",
        "evolution", "genome", "crispr", "particle",
    ],
    "Health": [
        "health", "medicine", "hospital", "cancer", "virus", "pandemic",
        "drug", "fda", "who", "disease", "surgery", "mental health",
        "obesity", "nutrition",
    ],
    "Policy": [
        "election", "law", "policy", "court", "government", "congress",
        "parliament", "regulation", "vote", "democracy", "rights",
        "sanction", "treaty", "legislation", "senate", "constitution",
    ],
    "India": [
        "india", "indian", "modi", "delhi", "mumbai", "bangalore", "chennai",
        "kolkata", "hyderabad", "bjp", "congress party", "lok sabha",
        "rajya sabha", "rupee", "bse", "nse", "sensex", "nifty",
    ],
    "Sport": [
        "sport", "football", "soccer", "basketball", "tennis", "cricket",
        "rugby", "golf", "athletics", "olympic", "nfl", "nba", "nhl", "mlb",
        "premier league", "champions league", "formula 1", "f1", "transfer",
        "match", "tournament", "championship", "league", "athlete",
    ],
    "Entertainment": [
        "film", "movie", "cinema", "tv", "television", "streaming", "netflix",
        "disney", "hbo", "music", "album", "concert", "award", "oscar",
        "grammy", "emmy", "bafta", "celebrity", "actor", "director",
        "game", "gaming", "playstation", "xbox", "nintendo", "box office",
    ],
    "World": [],  # catch-all — must be last
}


def _compile(keywords: List[str]) -> Pattern[str] | None:
    """
    Build one alternation regex per domain, longest keyword first so that
    "large language model" wins over a bare "model" if both are ever listed.
    Returns None for the empty catch-all domain.
    """
    if not keywords:
        return None
    ordered = sorted(keywords, key=len, reverse=True)
    return re.compile(
        r"\b(?:" + "|".join(re.escape(k) for k in ordered) + r")\b",
        flags=re.IGNORECASE,
    )


# Compiled once at import — infer_domain runs per story on every render.
_DOMAIN_PATTERNS: List[tuple[str, Pattern[str]]] = [
    (domain, pattern)
    for domain, keywords in DOMAIN_KEYWORDS.items()
    if (pattern := _compile(keywords)) is not None
]


def infer_domain(topic_label: str) -> str:
    """
    Return the most specific domain for a given topic label string.

    First match wins, following DOMAIN_KEYWORDS order. Anything unmatched
    falls through to "World", which is the intended catch-all.
    """
    for domain, pattern in _DOMAIN_PATTERNS:
        if pattern.search(topic_label):
            return domain
    return "World"
