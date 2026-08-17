"""
services/personalization/profiler.py
--------------------------------------
Build / update the UserProfile from accumulated ReadingEvent data.

The profile captures:
  - topic_weights  : which topics the user reads most deeply (time × scroll depth)
  - source_affinity: sources of the most deeply-read articles
  - preferred_depth: average scroll depth across all sessions

Called once at the end of the daily pipeline before newsletter adaptation.

Usage:
    from services.personalization.profiler import build_profile
    profile = await build_profile(session)
"""
import logging
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.db.orm_models import ArticleORM, ReadingEventORM, StoryClusterORM, UserProfileORM
from core.schemas.models import UserProfile
from core.utils import slugify

log = logging.getLogger(__name__)


async def build_profile(session: AsyncSession) -> UserProfile:
    """
    Read the last N days of ReadingEvents from the DB, compute engagement
    signals, and upsert the UserProfile row (id=1).
    Returns the updated UserProfile dataclass.
    """
    cutoff = date.today() - timedelta(days=settings.profile_window_days)

    result = await session.execute(
        select(ReadingEventORM).where(ReadingEventORM.event_date >= cutoff)
    )
    events = result.scalars().all()

    if not events:
        log.info("No reading events found — returning default profile")
        return UserProfile()

    # ── Topic weights (avg engagement score per topic) ────────────────────
    # Engagement score = normalised time × normalised scroll
    topic_time: dict[str, list[float]] = defaultdict(list)
    topic_scroll: dict[str, list[float]] = defaultdict(list)

    for event in events:
        slug = event.topic_slug
        topic_time[slug].append(float(event.time_spent_seconds))
        topic_scroll[slug].append(float(event.scroll_percent))

    # Compute engagement score per topic
    engagement: dict[str, float] = {}
    for slug in topic_time:
        avg_time = sum(topic_time[slug]) / len(topic_time[slug])
        avg_scroll = sum(topic_scroll[slug]) / len(topic_scroll[slug])
        # Normalise: time capped at 600s; scroll 0-100
        engagement[slug] = (min(avg_time, 600) / 600) * 0.6 + (avg_scroll / 100) * 0.4

    # Normalise to sum = 1
    total = sum(engagement.values()) or 1.0
    topic_weights = {k: round(v / total, 4) for k, v in sorted(
        engagement.items(), key=lambda x: -x[1]
    )}

    # ── Source affinity ───────────────────────────────────────────────────
    # For events with high engagement (score > 0.4), find which sources the
    # user reads most. We join ReadingEvent topic_slugs → StoryCluster.topic_label
    # → Article.source (cluster_id is a UUID; we can't compare it to a slug directly).
    source_scores: dict[str, list[float]] = defaultdict(list)
    high_engagement_slugs = {s for s, sc in engagement.items() if sc > 0.4}

    if high_engagement_slugs:
        # Fetch recent clusters; match those whose slugified label is in high_engagement_slugs
        cluster_result = await session.execute(
            select(StoryClusterORM.cluster_id, StoryClusterORM.topic_label).where(
                StoryClusterORM.cluster_date >= cutoff
            )
        )
        # Single pass: collect matching cluster IDs and build score lookup simultaneously
        matching_ids: list = []
        cid_to_score: dict[str, float] = {}
        for cid, label in cluster_result.fetchall():
            slug = slugify(label)
            if slug in high_engagement_slugs:
                matching_ids.append(cid)
                cid_to_score[str(cid)] = engagement[slug]

        if matching_ids:
            art_result = await session.execute(
                select(ArticleORM.source, ArticleORM.cluster_id).where(
                    ArticleORM.cluster_id.in_(matching_ids)
                )
            )
            for source, cid in art_result.fetchall():
                source_scores[source].append(cid_to_score.get(str(cid), 1.0))

    source_affinity = {
        src: round(sum(scores) / len(scores), 4)
        for src, scores in source_scores.items()
    }

    # ── Preferred depth ────────────────────────────────────────────────────
    preferred_depth = sum(e.scroll_percent for e in events) / len(events)

    # ── Persist ────────────────────────────────────────────────────────────
    orm = await session.get(UserProfileORM, 1)
    if orm is None:
        orm = UserProfileORM(id=1)
        session.add(orm)

    orm.topic_weights = topic_weights
    orm.source_affinity = source_affinity
    orm.preferred_depth = round(preferred_depth, 2)

    await session.flush()
    log.info(
        "Profile updated: %d topics tracked, preferred depth=%.1f%%",
        len(topic_weights),
        preferred_depth,
    )

    return UserProfile(
        topic_weights=topic_weights,
        source_affinity=source_affinity,
        preferred_depth=round(preferred_depth, 2),
    )


async def load_profile(session: AsyncSession) -> UserProfile:
    """Load the existing UserProfile from the DB (id=1)."""
    orm = await session.get(UserProfileORM, 1)
    if orm is None:
        return UserProfile()
    return UserProfile(
        topic_weights=orm.topic_weights or {},
        source_affinity=orm.source_affinity or {},
        preferred_depth=orm.preferred_depth or 50.0,
    )
