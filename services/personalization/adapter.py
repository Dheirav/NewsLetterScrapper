"""
services/personalization/adapter.py
--------------------------------------
Adapt the ordered list of KnowledgeStory objects to match the user's profile.

Rules applied in order:
  1. Stories whose topic slug matches a high-weight profile topic float to the top
  2. Stories below the engagement threshold get a lightweight "awareness" treatment
     (talking_points trimmed to 3, implications kept short)
  3. Global-awareness stories (tier-1 sources, multi-source) are always kept
     regardless of personal relevance

Usage:
    from services.personalization.adapter import adapt_newsletter
    ordered_stories = adapt_newsletter(stories, profile)
"""
import logging
from typing import List

from core.schemas.models import KnowledgeStory, UserProfile
from core.utils import slugify

log = logging.getLogger(__name__)

# Engagement weight above which a topic gets full-depth treatment
HIGH_ENGAGEMENT_THRESHOLD = 0.15
# Minimum source count to always include a story (global awareness)
GLOBAL_AWARENESS_MIN_SOURCES = 3
# Number of talking points for low-engagement stories
LOW_ENGAGEMENT_TALKING_POINTS = 3


def adapt_newsletter(
    stories: List[KnowledgeStory],
    profile: UserProfile,
) -> List[KnowledgeStory]:
    """
    Reorder and trim stories based on reading preferences.
    Returns the adapted, ordered list.
    """
    if not profile.topic_weights:
        # No profile data yet — return stories as-is
        log.info("No profile data — returning stories in default order")
        return stories

    def _score(story: KnowledgeStory) -> float:
        slug = slugify(story.topic_label)
        personal_weight = profile.topic_weights.get(slug, 0.0)
        # Bonus for global-awareness stories (many cross-confirming sources)
        global_bonus = 0.2 if story.source_count >= GLOBAL_AWARENESS_MIN_SOURCES else 0.0
        return personal_weight + global_bonus

    # Sort: highest-scoring stories first
    ordered = sorted(stories, key=_score, reverse=True)

    # Adaptive threshold: when a user tracks many topics, normalized weights are
    # naturally small (sum = 1 across N topics), so a fixed 0.15 threshold would
    # demote everything. Use a per-topic fair share when tracking > 5 topics.
    n = len(profile.topic_weights)
    effective_threshold = (
        min(HIGH_ENGAGEMENT_THRESHOLD, 1.0 / n * 0.8) if n > 5 else HIGH_ENGAGEMENT_THRESHOLD
    )

    # Trim depth for low-engagement topics
    for story in ordered:
        slug = slugify(story.topic_label)
        weight = profile.topic_weights.get(slug, 0.0)
        is_global = story.source_count >= GLOBAL_AWARENESS_MIN_SOURCES

        if weight < effective_threshold and not is_global:
            # Awareness-layer treatment: trim talking points
            story.talking_points = story.talking_points[:LOW_ENGAGEMENT_TALKING_POINTS]

    high_depth = sum(
        1 for s in ordered
        if profile.topic_weights.get(slugify(s.topic_label), 0.0) >= effective_threshold
    )
    log.info(
        "Adapted newsletter: %d stories (%d high-depth, %d awareness-layer)",
        len(ordered),
        high_depth,
        len(ordered) - high_depth,
    )
    return ordered
