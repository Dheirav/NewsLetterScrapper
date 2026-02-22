"""
core/utils.py
--------------
Shared utility functions used across multiple services.
"""
import re


def slugify(text: str) -> str:
    """
    Convert a topic label into a URL/key-safe slug.

    Examples:
        "US-China Trade Tariffs" → "us-china-trade-tariffs"
        "AI & Machine Learning" → "ai-machine-learning"

    Used by:
      - services/personalization/profiler.py  (building topic_weights keys)
      - services/personalization/adapter.py   (matching stories to profile weights)
    """
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"[\s_-]+", "-", text).strip("-")
