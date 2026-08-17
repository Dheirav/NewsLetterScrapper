"""
services/ingestion/semantic_dedup.py
--------------------------------------
Catch stories that were already briefed, republished under a new URL.

The two earlier dedup stages cannot see these. `deduplicator.deduplicate()`
compares exact URLs against the database and TF-IDF titles *within the current
batch*, so a piece that reappears the next day with a fresh URL — wire copy, a
retitled feature, a syndicated repost — passes both and gets clustered again.

This stage runs after embedding and compares each new article's vector against
articles already stored in a recent window, using pgvector cosine distance.

Choosing the threshold
----------------------
Measured against 13,836 real articles, cross-day nearest neighbours fall into
two clearly different groups:

    1.0000  Foreign Affairs, "How Iran Should End the War", 04-04 and 04-07
    0.9977  Ars Technica -> Hacker News, same post syndicated
    0.9930  New Scientist, same piece retitled
    ------------------------------------------------ true duplicates above
    0.9832  STAT News "Up and down the ladder" — a recurring column whose
            content differs every week
    0.9647  Ars Technica AND Wired on Colorado right-to-repair
    0.9642  Hindustan Times AND Times of India on the same defence contract

The bottom group must survive. Independent outlets covering one event is not
duplication — it is the signal clustering exists to find, and the input to the
cross-confirmation grading in `knowledge/reliability.py`. Collapsing those
pairs would make stories look less corroborated than they are.

The default of 0.985 sits above the recurring-column false positive and well
clear of genuine multi-source coverage.

Usage:
    from services.ingestion.semantic_dedup import mark_semantic_duplicates
    fresh = await mark_semantic_duplicates(embedded_articles, session)
"""
import logging
from typing import List

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.schemas.models import Article

log = logging.getLogger(__name__)

# Nearest STRICTLY EARLIER neighbour within the lookback window.
#
# The window is anchored on the candidate's own created_at rather than NOW() so
# the result does not depend on when the query happens to run — reprocessing an
# older batch behaves identically to processing it live.
#
# "Earlier" is required, not incidental: without it a story could be recorded as
# a duplicate of one published after it, and two articles arriving in the same
# batch could each be marked against the other. Ties on the timestamp fall back
# to the id so the ordering is always total.
#
# Already-marked duplicates are excluded so chains collapse onto one original
# rather than forming a linked list.
# Resolved for the whole batch in one statement via LATERAL, rather than a
# lookup per article: the vectors are already stored, so there is no reason to
# serialise 768 floats per candidate over the wire and pay a round-trip each.
#
# make_interval(days => :days) rather than CAST(:window AS interval) — asyncpg
# prepares the statement and then cannot encode a Python string into an
# interval parameter.
_FIND_DUPLICATES = text(
    """
    WITH candidates AS (
        SELECT id, created_at, embedding
        FROM articles
        WHERE id = ANY(:ids) AND embedding IS NOT NULL
    )
    SELECT c.id AS dup_id, n.id AS orig_id, n.title, n.source, n.sim
    FROM candidates c
    JOIN LATERAL (
        SELECT b.id, b.title, b.source, 1 - (b.embedding <=> c.embedding) AS sim
        FROM articles b
        WHERE b.embedding IS NOT NULL
          AND b.duplicate_of IS NULL
          AND b.created_at >= c.created_at - make_interval(days => :days)
          AND (
                b.created_at < c.created_at
                OR (b.created_at = c.created_at AND b.id < c.id)
              )
        ORDER BY b.embedding <=> c.embedding
        LIMIT 1
    ) n ON TRUE
    WHERE n.sim >= :threshold
    """
)


async def mark_semantic_duplicates(
    articles: List[Article],
    session: AsyncSession,
    threshold: float | None = None,
    lookback_days: int | None = None,
) -> List[Article]:
    """
    Mark near-identical restatements of earlier articles and return only the
    articles that should continue into clustering.

    Articles without an embedding or without a database id are passed through
    untouched — there is nothing to compare them against.
    """
    if threshold is None:
        threshold = settings.dedup_semantic_threshold
    if lookback_days is None:
        lookback_days = settings.dedup_lookback_days

    candidates = [a for a in articles if a.embedding is not None and a.id]
    if not candidates:
        return articles

    log.info(
        "Semantic dedup: checking %d articles against the last %d days (threshold %.3f)",
        len(candidates), lookback_days, threshold,
    )

    by_id = {a.id: a for a in candidates}
    matches = (
        await session.execute(
            _FIND_DUPLICATES,
            {
                "ids": list(by_id),
                "days": lookback_days,
                "threshold": threshold,
            },
        )
    ).fetchall()

    duplicate_ids: set[int] = set()
    for dup_id, orig_id, orig_title, orig_source, sim in matches:
        duplicate_ids.add(dup_id)
        dup = by_id[dup_id]
        log.info(
            "  republished (%.4f): [%s] %s  ->  [%s] %s",
            sim, dup.source, dup.title[:52], orig_source, orig_title[:52],
        )

    if duplicate_ids:
        await session.execute(
            text(
                "UPDATE articles SET duplicate_of = m.orig "
                "FROM (SELECT unnest(CAST(:dups AS bigint[])) AS dup, "
                "             unnest(CAST(:origs AS bigint[])) AS orig) AS m "
                "WHERE articles.id = m.dup"
            ),
            {
                "dups": [r[0] for r in matches],
                "origs": [r[1] for r in matches],
            },
        )
        await session.flush()

    fresh = [a for a in articles if a.id not in duplicate_ids]
    log.info(
        "Semantic dedup: %d marked as republished, %d articles continue",
        len(duplicate_ids), len(fresh),
    )
    return fresh
