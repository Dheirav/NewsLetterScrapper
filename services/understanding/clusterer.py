"""
services/understanding/clusterer.py
--------------------------------------
Group articles about the same real-world event using HDBSCAN on embeddings.

Algorithm:
  - Build a numpy matrix of article embeddings
  - Fit HDBSCAN with cosine metric (no need to specify k in advance)
  - Articles labelled -1 (noise) become individual singleton clusters
  - Returns List[StoryCluster] with articles assigned

Usage:
    from services.understanding.clusterer import cluster_articles
    clusters = cluster_articles(articles)
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import List

import numpy as np
from hdbscan import HDBSCAN

from core.schemas.models import Article, StoryCluster

log = logging.getLogger(__name__)

# Minimum number of articles to form a multi-article cluster.
# Articles that don't fit any cluster become singleton clusters.
MIN_CLUSTER_SIZE = 2


def _cluster_articles_sync(articles: List[Article]) -> List[StoryCluster]:
    """
    Cluster articles by semantic similarity of their embeddings.
    Articles without embeddings are placed in their own singleton clusters.

    Returns a list of StoryCluster objects (topic_label is set to a placeholder;
    labeler.py will replace it with an LLM-generated label).
    """
    # Separate articles with / without embeddings
    embedded = [a for a in articles if a.embedding is not None]
    no_embed = [a for a in articles if a.embedding is None]

    log.info(
        "Clustering %d articles (%d without embeddings → singletons) …",
        len(embedded),
        len(no_embed),
    )

    now = datetime.now(tz=timezone.utc)
    clusters: List[StoryCluster] = []

    if embedded:
        matrix = np.array([a.embedding for a in embedded], dtype=np.float32)

        # Normalize for cosine similarity
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1.0, norms)
        matrix_normed = matrix / norms

        hdb = HDBSCAN(
            min_cluster_size=MIN_CLUSTER_SIZE,
            metric="euclidean",   # euclidean on L2-normalised vecs ≡ cosine distance
            cluster_selection_method="eom",
        )
        labels = hdb.fit_predict(matrix_normed)

        # Group by cluster label
        cluster_map: dict[int, List[Article]] = {}
        for article, label in zip(embedded, labels):
            cluster_map.setdefault(int(label), []).append(article)

        for label, members in cluster_map.items():
            if label == -1:
                # HDBSCAN noise → break into singletons
                for article in members:
                    cid = str(uuid.uuid4())
                    article.cluster_id = cid
                    clusters.append(
                        StoryCluster(
                            cluster_id=cid,
                            topic_label=article.title[:80],
                            articles=[article],
                            created_at=now,
                        )
                    )
            else:
                cid = str(uuid.uuid4())
                for article in members:
                    article.cluster_id = cid
                clusters.append(
                    StoryCluster(
                        cluster_id=cid,
                        topic_label=members[0].title[:80],  # placeholder
                        articles=members,
                        created_at=now,
                    )
                )

    # Articles without embeddings → singletons
    for article in no_embed:
        cid = str(uuid.uuid4())
        article.cluster_id = cid
        clusters.append(
            StoryCluster(
                cluster_id=cid,
                topic_label=article.title[:80],
                articles=[article],
                created_at=now,
            )
        )

    multi = sum(1 for c in clusters if len(c.articles) > 1)
    log.info(
        "Clustering complete: %d clusters (%d multi-article, %d singletons)",
        len(clusters),
        multi,
        len(clusters) - multi,
    )
    return clusters


async def cluster_articles(articles: List[Article]) -> List[StoryCluster]:
    """
    Async wrapper around the synchronous HDBSCAN clustering.
    Runs the CPU-bound work in a thread pool so the event loop stays free.
    """
    import asyncio
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _cluster_articles_sync, articles)
