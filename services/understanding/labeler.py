"""
services/understanding/labeler.py
------------------------------------
Generate a concise, descriptive topic label for each StoryCluster using the
local Ollama LLM.

For singleton clusters the article title is used directly (no LLM call needed).

Usage:
    from services.understanding.labeler import label_clusters
    clusters = await label_clusters(clusters)
"""
import asyncio
import logging
from typing import List

import ollama
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from core.config import settings
from core.schemas.models import StoryCluster

log = logging.getLogger(__name__)

_LABEL_PROMPT = """\
You will be given a list of news article titles. They all cover the same real-world event.
Generate a single concise topic label (5–10 words maximum) that best describes the shared event.
The label should be informative and specific, not generic.

Article titles:
{titles}

Topic label (output ONLY the label, no punctuation, no explanation):"""


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
def _generate_label_sync(titles: List[str]) -> str:
    """Synchronous Ollama chat call — run in a thread pool, with auto-retry."""
    prompt = _LABEL_PROMPT.format(titles="\n".join(f"- {t}" for t in titles))
    response = ollama.chat(
        model=settings.ollama_llm_model,
        messages=[{"role": "user", "content": prompt}],
        options={"temperature": 0.3, "num_predict": 30,
                 "num_ctx": settings.ollama_num_ctx},
    )
    label = response["message"]["content"].strip().strip('"').strip("'")
    # Trim to a safe length
    return label[:120] if label else titles[0][:80]


# Max simultaneous Ollama label calls — prevents overloading local inference
_LABEL_CONCURRENCY = 5


async def label_clusters(clusters: List[StoryCluster]) -> List[StoryCluster]:
    """
    Assign LLM-generated topic labels to multi-article clusters.
    Singleton clusters keep their article title as the label.
    Returns the same list with .topic_label updated in-place.

    Concurrency is capped at _LABEL_CONCURRENCY to avoid flooding the local
    Ollama server with too many simultaneous requests.
    """
    loop = asyncio.get_running_loop()
    semaphore = asyncio.Semaphore(_LABEL_CONCURRENCY)
    multi_article = [c for c in clusters if len(c.articles) > 1]

    log.info("Generating labels for %d multi-article clusters …", len(multi_article))

    async def label_one(cluster: StoryCluster) -> None:
        async with semaphore:
            try:
                titles = [a.title for a in cluster.articles[:10]]  # cap at 10 titles
                label = await loop.run_in_executor(None, _generate_label_sync, titles)
                cluster.topic_label = label
            except Exception as exc:
                log.warning(
                    "Label generation failed for cluster %s: %s", cluster.cluster_id, exc
                )

    await asyncio.gather(*[label_one(c) for c in multi_article])
    log.info("Cluster labelling complete")
    return clusters
