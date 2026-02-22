"""
services/newsletter/_domains.py
---------------------------------
Single source of truth for domain keyword mapping.
Used by both assembler.py (for logging) and renderer.py (for Jinja2 filter).
"""
from typing import Dict, List

# SECTION_ORDER controls the sequence of sections in the rendered newsletter.
SECTION_ORDER = ["World", "AI", "Technology", "Economy", "Science", "Policy", "Health", "Other"]

# Keyword lists for domain inference from topic_label text.
# Precedence: first match wins, in dict iteration order.
DOMAIN_KEYWORDS: Dict[str, List[str]] = {
    "AI": [
        "artificial intelligence", " ai ", " llm", "large language model",
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
    "World": [],  # catch-all — must be last
}


def infer_domain(topic_label: str) -> str:
    """Return the most specific domain for a given topic label string."""
    label_lower = f" {topic_label.lower()} "  # pad to help word-boundary matching
    for domain, keywords in DOMAIN_KEYWORDS.items():
        if any(kw in label_lower for kw in keywords):
            return domain
    return "World"
