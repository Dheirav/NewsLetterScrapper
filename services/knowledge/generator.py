"""
services/knowledge/generator.py
---------------------------------
Generate structured deep-knowledge stories from StoryCluster objects.

Two generation modes are available — pass mode= to generate_knowledge_stories():

  mode="concise"  (default)
      One LLM call per cluster returns a JSON object with all five sections.
      Fast (~5× fewer calls). Good for daily runs where throughput matters.

  mode="detailed"
      Five sequential LLM calls per cluster, one per section.
      Slower but each call gets the model's full token budget for a single
      section, producing richer, more analytically thorough output.
      Useful for high-value clusters or weekly deep-dives.

Usage:
    from services.knowledge.generator import generate_knowledge_stories

    # Fast daily run (default)
    stories = await generate_knowledge_stories(clusters, session, run_date=today)

    # In-depth weekly run
    stories = await generate_knowledge_stories(
        clusters, session, run_date=today, mode="detailed"
    )
"""
import asyncio
import json
import logging
import re
from datetime import date
from typing import List, Literal

import ollama
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from core.config import settings
from core.db.orm_models import FailedGenerationORM, KnowledgeStoryORM
from core.schemas.models import KnowledgeStory, StoryCluster
from services.knowledge.prompts import (
    COMBINED_STORY_PROMPT,
    EXECUTIVE_SUMMARY_PROMPT,
    CONTEXT_PROMPT,
    WHY_IT_MATTERS_PROMPT,
    IMPLICATIONS_PROMPT,
    TALKING_POINTS_PROMPT,
    build_articles_text,
)
from services.knowledge.reliability import assess_reliability
from services.knowledge.repository import save_knowledge_story

log = logging.getLogger(__name__)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=15),
    # JSONDecodeError is retried alongside the transport errors. A malformed
    # response is transient in exactly the same way a dropped connection is —
    # the next sample usually parses — but without this a single truncated
    # string loses the whole story to the dead-letter queue. Measured at 1 in 5
    # when the prompt asks for longer output.
    retry=retry_if_exception_type(
        (ConnectionError, TimeoutError, OSError, json.JSONDecodeError)
    ),
    reraise=True,
)
def _chat_json_sync(prompt: str) -> dict:
    """
    CONCISE mode: single Ollama call returning a parsed JSON dict with all five
    story sections. Executed in a thread pool with auto-retry.
    """
    response = ollama.chat(
        model=settings.ollama_llm_model,
        messages=[{"role": "user", "content": prompt}],
        # 2000, not 900: the prompt now asks for ~250 words across four sections
        # plus five talking points. At 900 the JSON is cut mid-string and the
        # whole story is lost, which is what the retry above exists to survive.
        options={"temperature": 0.4, "num_predict": 2000,
                 "num_ctx": settings.ollama_num_ctx},
        format="json",
    )
    raw = response["message"]["content"].strip()
    # Strip any accidental markdown fences
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=15),
    retry=retry_if_exception_type((ConnectionError, TimeoutError, OSError)),
    reraise=True,
)
def _chat_text_sync(prompt: str) -> str:
    """
    DETAILED mode: single focused Ollama call returning plain text for one
    section. Executed in a thread pool with auto-retry.
    """
    response = ollama.chat(
        model=settings.ollama_llm_model,
        messages=[{"role": "user", "content": prompt}],
        options={"temperature": 0.4, "num_predict": 600,
                 "num_ctx": settings.ollama_num_ctx},
    )
    return response["message"]["content"].strip()


def _parse_talking_points(raw: str) -> List[str]:
    """Extract numbered list items from LLM output."""
    lines = []
    for line in raw.splitlines():
        line = line.strip()
        # Match "1. ...", "2) ...", "- ..." patterns
        match = re.match(r"^(?:\d+[.)]|\-)\s+(.+)$", line)
        if match:
            lines.append(match.group(1).strip())
    if not lines:
        # Fallback: split by newlines
        lines = [l.strip() for l in raw.splitlines() if l.strip()]
    return lines[:6]  # cap at 6 bullets


# Below this, a story's talking points are worth one extra call to recover.
# The adapter trims low-engagement stories to three, so three is the floor at
# which the section still reads as intended.
MIN_TALKING_POINTS = 3


async def _backfill_talking_points(
    cluster: StoryCluster, existing: List[str]
) -> List[str]:
    """
    Re-request only the talking points, using detailed mode's focused prompt.

    Cheaper and less wasteful than retrying the whole JSON call: the other four
    sections were fine, and this prompt asks for one thing so the model is far
    less likely to collapse the list. Falls back to whatever was already parsed
    if the second attempt is no better.
    """
    try:
        loop = asyncio.get_running_loop()
        raw = await loop.run_in_executor(
            None,
            _chat_text_sync,
            TALKING_POINTS_PROMPT.format(
                topic_label=cluster.topic_label,
                articles_text=build_articles_text(cluster.articles),
            ),
        )
        recovered = _parse_talking_points(raw)
    except Exception as exc:
        log.warning(
            "talking-point backfill failed for '%s': %s", cluster.topic_label[:50], exc
        )
        return existing

    if len(recovered) > len(existing):
        log.info(
            "  backfilled talking points for '%s': %d -> %d",
            cluster.topic_label[:50], len(existing), len(recovered),
        )
        return recovered
    return existing


def cluster_coherence(cluster: StoryCluster) -> float:
    """
    Mean pairwise cosine similarity between a cluster's article embeddings.

    Separates events from topic buckets. A real event scores high because every
    article describes the same thing; a bucket scores low because the only thing
    its members share is a section of the newspaper. Two observed extremes:

        0.68  X-Men casting / a Netflix thriller / a BBC drama trailer
        0.65  cheap laptops / eclipse planning / a USB necklace

    Asking "what happened, who is involved" of either produces mush, and no
    prompt or context window fixes a cluster that is not a story.

    Returns 1.0 for a single article — trivially self-consistent, and singletons
    are filtered earlier by min_cluster_articles anyway. Returns 1.0 when
    embeddings are missing rather than 0.0, so a missing vector cannot silently
    suppress a story.
    """
    # `is not None`, never truthiness. Embeddings arrive as Python lists from
    # the embedder but as numpy arrays when reloaded from pgvector on the step-5
    # resume path, and bool(ndarray) raises "truth value of an array ... is
    # ambiguous". Every other consumer in the pipeline already gets this right;
    # this one crashed the whole run after a restart.
    vectors = [
        a.embedding for a in cluster.articles
        if a.embedding is not None and len(a.embedding) > 0
    ]
    if len(vectors) < 2:
        return 1.0

    import numpy as np

    matrix = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    matrix = matrix / np.where(norms == 0, 1.0, norms)
    sims = matrix @ matrix.T
    upper = np.triu_indices(len(matrix), k=1)
    return float(sims[upper].mean())


def _filter_incoherent(clusters: List[StoryCluster], threshold: float) -> List[StoryCluster]:
    """
    Drop clusters that are not events, and log the full spread either way.

    The logging is the point as much as the filter: the threshold was chosen
    from a single day's distribution, so every run needs to report where the
    clusters actually fell for it to be tuned against real data later.
    """
    scored = [(c, cluster_coherence(c)) for c in clusters]
    if not scored:
        return []

    values = sorted(s for _, s in scored)
    log.info(
        "Cluster coherence: min %.3f, median %.3f, max %.3f (threshold %.2f)",
        values[0], values[len(values) // 2], values[-1], threshold,
    )

    kept, dropped = [], []
    for cluster, score in scored:
        (kept if score >= threshold else dropped).append((cluster, score))

    for cluster, score in sorted(dropped, key=lambda x: x[1]):
        log.info(
            "  not an event (%.3f), skipping: '%s'", score, cluster.topic_label[:60]
        )
    if dropped:
        log.info(
            "Dropped %d of %d clusters below coherence %.2f — no LLM calls spent on them",
            len(dropped), len(scored), threshold,
        )
    return [c for c, _ in kept]


async def _generate_one_concise(cluster: StoryCluster) -> KnowledgeStory | None:
    """
    CONCISE mode: generate all five knowledge sections in a single LLM call
    that returns a structured JSON object.

    DB persistence is intentionally excluded here so that multiple concurrent
    invocations do not race on the same AsyncSession.
    """
    articles_text = build_articles_text(
        cluster.articles,
        max_chars_per_article=settings.knowledge_chars_per_article,
        max_articles=settings.knowledge_max_articles,
    )
    prompt = COMBINED_STORY_PROMPT.format(
        topic_label=cluster.topic_label,
        articles_text=articles_text,
    )

    try:
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(None, _chat_json_sync, prompt)

        exec_summary = str(data.get("executive_summary", "")).strip()
        context = str(data.get("context", "")).strip()
        why_it_matters = str(data.get("why_it_matters", "")).strip()
        implications = str(data.get("implications", "")).strip()
        talking_points_raw = data.get("talking_points", [])

        # talking_points may arrive as list[str] or as a raw string
        if isinstance(talking_points_raw, list):
            talking_points = [str(t).strip() for t in talking_points_raw if t][:6]
        else:
            talking_points = _parse_talking_points(str(talking_points_raw))

        if not exec_summary:
            raise ValueError("LLM returned empty executive_summary")

        # Models routinely collapse the talking_points array in JSON mode even
        # when the prose sections come back well-formed — measured at 5 of 30
        # stories returning a single bullet against a prompt asking for five.
        # Rather than discard four good sections and pay for a full retry, ask
        # again for just this field using the focused prompt from detailed mode.
        if len(talking_points) < MIN_TALKING_POINTS:
            talking_points = await _backfill_talking_points(cluster, talking_points)

        reliability_notes = assess_reliability(cluster)

        story = KnowledgeStory(
            cluster_id=cluster.cluster_id,
            topic_label=cluster.topic_label,
            executive_summary=exec_summary,
            context=context,
            why_it_matters=why_it_matters,
            implications=implications,
            talking_points=talking_points,
            reliability_notes=reliability_notes,
            source_count=len({a.source for a in cluster.articles}),
            article_urls=[a.url for a in cluster.articles],
            article_sources=[a.source for a in cluster.articles],
        )

        log.info("[concise] Generated story: '%s'", cluster.topic_label[:60])
        return story

    except Exception as exc:
        log.error(
            "[concise] Generation failed for '%s': %s",
            cluster.topic_label[:60],
            exc,
        )
        return None


async def _generate_one_detailed(cluster: StoryCluster) -> KnowledgeStory | None:
    """
    DETAILED mode: five sequential LLM calls, one per knowledge section.
    Each call has the model's full token budget focused on a single section,
    producing richer, more analytically thorough output than the concise mode.

    DB persistence is intentionally excluded here.
    """
    articles_text = build_articles_text(
        cluster.articles,
        max_chars_per_article=1500,  # more material per article for focused calls
        max_articles=6,
    )
    fmt = dict(topic_label=cluster.topic_label, articles_text=articles_text)

    try:
        loop = asyncio.get_running_loop()

        # Five focused calls run sequentially so the model doesn't compete with
        # itself on the same GPU/CPU resource.
        exec_summary = await loop.run_in_executor(
            None, _chat_text_sync, EXECUTIVE_SUMMARY_PROMPT.format(**fmt)
        )
        context = await loop.run_in_executor(
            None, _chat_text_sync, CONTEXT_PROMPT.format(**fmt)
        )
        why_it_matters = await loop.run_in_executor(
            None, _chat_text_sync, WHY_IT_MATTERS_PROMPT.format(**fmt)
        )
        implications = await loop.run_in_executor(
            None, _chat_text_sync, IMPLICATIONS_PROMPT.format(**fmt)
        )
        raw_points = await loop.run_in_executor(
            None, _chat_text_sync, TALKING_POINTS_PROMPT.format(**fmt)
        )
        talking_points = _parse_talking_points(raw_points)

        if not exec_summary:
            raise ValueError("LLM returned empty executive_summary")

        reliability_notes = assess_reliability(cluster)

        story = KnowledgeStory(
            cluster_id=cluster.cluster_id,
            topic_label=cluster.topic_label,
            executive_summary=exec_summary,
            context=context,
            why_it_matters=why_it_matters,
            implications=implications,
            talking_points=talking_points,
            reliability_notes=reliability_notes,
            source_count=len({a.source for a in cluster.articles}),
            article_urls=[a.url for a in cluster.articles],
            article_sources=[a.source for a in cluster.articles],
        )

        log.info("[detailed] Generated story: '%s'", cluster.topic_label[:60])
        return story

    except Exception as exc:
        log.error(
            "[detailed] Generation failed for '%s': %s",
            cluster.topic_label[:60],
            exc,
        )
        return None


async def generate_knowledge_stories(
    clusters: List[StoryCluster],
    session: AsyncSession,
    run_date: date,
    max_concurrent: int = 5,
    mode: Literal["concise", "detailed"] = "concise",
) -> List[KnowledgeStory]:
    """
    Generate knowledge stories for multi-article clusters only.
    Singleton clusters are skipped — they carry too little signal for deep analysis.

    mode="concise"  — one JSON call per cluster (default, fast daily use)
    mode="detailed" — five focused calls per cluster (richer output, ~5× slower)

    ``run_date`` is the date the pipeline run started. It stamps both the saved
    stories and any dead-letter rows, so a run that crosses midnight keeps all
    of its output on one date instead of splitting across two.

    Limits concurrent Ollama calls to avoid overloading local inference.
    """
    meaningful = [c for c in clusters if len(c.articles) >= settings.min_cluster_articles]
    skipped = len(clusters) - len(meaningful)
    if skipped:
        log.info(
            "Skipping %d singleton clusters — %d multi-article clusters will be processed",
            skipped, len(meaningful),
        )

    # Coherence gate first, so the top-N cap below ranks among real events
    # rather than spending its budget on topic buckets that happen to be large.
    meaningful = _filter_incoherent(meaningful, settings.min_cluster_coherence)

    # rank by richness and keep top N; tail clusters are low-signal
    meaningful.sort(key=lambda c: len(c.articles), reverse=True)
    if len(meaningful) > settings.max_knowledge_clusters:
        log.info(
            "Capping at top %d clusters by article count (dropped %d low-signal clusters)",
            settings.max_knowledge_clusters,
            len(meaningful) - settings.max_knowledge_clusters,
        )
        meaningful = meaningful[:settings.max_knowledge_clusters]

    # #4 — skip clusters that already have a saved story (safe re-run / crash recovery)
    if meaningful:
        existing_result = await session.execute(
            select(KnowledgeStoryORM.cluster_id).where(
                KnowledgeStoryORM.cluster_id.in_([c.cluster_id for c in meaningful])
            )
        )
        existing_ids = {row[0] for row in existing_result}
        if existing_ids:
            log.info("Skipping %d clusters — stories already saved", len(existing_ids))
            meaningful = [c for c in meaningful if c.cluster_id not in existing_ids]

    clusters = meaningful
    _generate_fn = _generate_one_concise if mode == "concise" else _generate_one_detailed
    log.info(
        "Generating knowledge stories for %d clusters … (mode=%s)",
        len(clusters),
        mode,
    )
    semaphore = asyncio.Semaphore(max_concurrent)

    async def guarded(cluster: StoryCluster) -> tuple[StoryCluster, "KnowledgeStory | None"]:
        async with semaphore:
            return cluster, await _generate_fn(cluster)

    pairs = await asyncio.gather(*[guarded(c) for c in clusters])
    stories: list[KnowledgeStory] = []

    # Persist sequentially — AsyncSession must not be accessed from concurrent coroutines
    for cluster, story in pairs:
        if story is not None:
            await save_knowledge_story(story, session, run_date)
            stories.append(story)
        else:
            # Dead-letter: record the failed cluster for manual inspection/retry
            failed = FailedGenerationORM(
                cluster_id=cluster.cluster_id,
                topic_label=cluster.topic_label,
                error_message="All LLM retries exhausted — see logs for details",
                mode=mode,
                run_date=run_date,
            )
            session.add(failed)
            log.warning(
                "Persisted failed generation to dead-letter queue: '%s'",
                cluster.topic_label[:60],
            )

    await session.flush()
    log.info("Knowledge generation complete: %d/%d stories", len(stories), len(clusters))
    return stories
