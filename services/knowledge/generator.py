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
    stories = await generate_knowledge_stories(clusters, session)

    # In-depth weekly run
    stories = await generate_knowledge_stories(clusters, session, mode="detailed")
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
    retry=retry_if_exception_type((ConnectionError, TimeoutError, OSError)),
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
        options={"temperature": 0.4, "num_predict": 900},
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
        options={"temperature": 0.4, "num_predict": 600},
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


async def _generate_one_concise(cluster: StoryCluster) -> KnowledgeStory | None:
    """
    CONCISE mode: generate all five knowledge sections in a single LLM call
    that returns a structured JSON object.

    DB persistence is intentionally excluded here so that multiple concurrent
    invocations do not race on the same AsyncSession.
    """
    articles_text = build_articles_text(cluster.articles)
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
    max_concurrent: int = 5,
    mode: Literal["concise", "detailed"] = "concise",
) -> List[KnowledgeStory]:
    """
    Generate knowledge stories for multi-article clusters only.
    Singleton clusters are skipped — they carry too little signal for deep analysis.

    mode="concise"  — one JSON call per cluster (default, fast daily use)
    mode="detailed" — five focused calls per cluster (richer output, ~5× slower)

    Limits concurrent Ollama calls to avoid overloading local inference.
    """
    meaningful = [c for c in clusters if len(c.articles) >= settings.min_cluster_articles]
    skipped = len(clusters) - len(meaningful)
    if skipped:
        log.info(
            "Skipping %d singleton clusters — %d multi-article clusters will be processed",
            skipped, len(meaningful),
        )

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
    run_date = date.today()

    # Persist sequentially — AsyncSession must not be accessed from concurrent coroutines
    for cluster, story in pairs:
        if story is not None:
            await save_knowledge_story(story, session)
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
