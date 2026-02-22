"""
services/ingestion/deduplicator.py
------------------------------------
Remove duplicate articles before database persistence.

Two-stage deduplication:
  1. Exact URL match against URLs already in the DB
  2. TF-IDF cosine similarity between normalised titles — O(n) matrix multiply
     instead of the previous O(n²) SequenceMatcher approach. With 500 articles
     this cuts 125,000 pairwise comparisons down to one vectorised matrix op.

Usage:
    from services.ingestion.deduplicator import deduplicate
    new_articles = await deduplicate(articles, session)
"""
import logging
import re
from typing import List, Set

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.db.orm_models import ArticleORM
from core.schemas.models import Article

log = logging.getLogger(__name__)


def _normalise(title: str) -> str:
    """Lowercase, strip punctuation and extra whitespace for fuzzy comparison."""
    title = title.lower()
    title = re.sub(r"[^\w\s]", "", title)
    return re.sub(r"\s+", " ", title).strip()


def _tfidf_dedup(articles: List[Article], threshold: float) -> List[Article]:
    """
    Within-batch title deduplication using TF-IDF cosine similarity.

    Builds a TF-IDF matrix from all normalised titles once, then compares each
    article against all preceding accepted articles in a single dot-product step,
    effectively O(n·k) where k grows slowly — vastly better than O(n²).

    Single-article batches are returned unchanged (TfidfVectorizer needs ≥2 docs
    for meaningful IDF, but a 1-item list is trivially unique anyway).
    """
    if len(articles) <= 1:
        return list(articles)

    norms = [_normalise(a.title) for a in articles]

    vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 4), min_df=1)
    try:
        tfidf_matrix = vectorizer.fit_transform(norms)
    except ValueError:
        # Fallback: all titles empty after normalisation → keep all
        return list(articles)

    unique_indices: list[int] = [0]

    for i in range(1, len(articles)):
        # Compare article i against all already-accepted articles at once
        accepted_matrix = tfidf_matrix[unique_indices]
        current_vec = tfidf_matrix[i]
        sims = cosine_similarity(current_vec, accepted_matrix)[0]
        if float(np.max(sims)) < threshold:
            unique_indices.append(i)

    return [articles[i] for i in unique_indices]


async def deduplicate(
    articles: List[Article],
    session: AsyncSession,
    threshold: float | None = None,
) -> List[Article]:
    """
    Return only articles that are genuinely new (not already in the DB and not
    near-duplicate of another article in the current batch).
    """
    if threshold is None:
        threshold = settings.dedup_title_threshold

    # ── Stage 1: remove URLs already in the DB ──────────────────────────────
    urls = [a.url for a in articles]
    result = await session.execute(select(ArticleORM.url).where(ArticleORM.url.in_(urls)))
    existing_urls: Set[str] = {row[0] for row in result.fetchall()}

    candidates = [a for a in articles if a.url not in existing_urls]
    log.info(
        "URL dedup: %d → %d articles (%d already in DB)",
        len(articles),
        len(candidates),
        len(existing_urls),
    )

    # ── Stage 2: TF-IDF title dedup within the current batch ────────────────
    unique = _tfidf_dedup(candidates, threshold)

    log.info(
        "Title dedup: %d → %d articles (%d near-duplicates removed)",
        len(candidates),
        len(unique),
        len(candidates) - len(unique),
    )
    return unique
