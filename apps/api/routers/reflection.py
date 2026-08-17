"""
apps/api/routers/reflection.py
---------------------------------
Routes:
  GET /api/reflection/today           — redirect to today's date
  GET /api/reflection/{date}          — return daily reading stats + AI summary
"""
import asyncio
import json
import logging
from collections import defaultdict
from datetime import date

import ollama
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.limiter import limiter
from core.config import settings
from core.db.orm_models import ReadingEventORM
from core.db.session import get_db
from fastapi.responses import RedirectResponse

log = logging.getLogger(__name__)
router = APIRouter()


class TopicStat(BaseModel):
    topic_slug: str
    time_spent_seconds: int
    avg_scroll_percent: float
    sessions: int


class ReflectionResponse(BaseModel):
    date: date
    total_reading_time_seconds: int
    avg_scroll_depth: float
    stories_read: int
    topics: list[TopicStat]
    ai_summary: str


def _generate_summary_sync(topics: list[TopicStat], total_time: int) -> str:
    """Ask Ollama to generate a brief 'today you learned' reflection."""
    topic_lines = "\n".join(
        f"- {t.topic_slug.replace('-', ' ').title()}: {t.time_spent_seconds}s, "
        f"{t.avg_scroll_percent:.0f}% scroll"
        for t in topics[:8]
    )
    prompt = (
        f"A person spent {total_time // 60} minutes reading today's intelligence briefing.\n"
        f"Topics they engaged with most:\n{topic_lines}\n\n"
        "Write a 2-3 sentence reflection starting with 'Today you deepened your understanding of…' "
        "that acknowledges what they learned and encourages continued engagement. "
        "Be specific, warm, and intellectually stimulating. Output ONLY the reflection."
    )
    try:
        response = ollama.chat(
            model=settings.ollama_llm_model,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.6, "num_predict": 150},
        )
        return response["message"]["content"].strip()
    except Exception as exc:
        log.warning("Reflection summary generation failed: %s", exc)
        return "Today you stayed informed. Keep building your understanding one day at a time."


@router.get("/today", response_class=RedirectResponse)
async def today_reflection_redirect():
    return RedirectResponse(url=f"/api/reflection/{date.today().isoformat()}")


@router.get(
    "/{reflection_date}",
    response_model=ReflectionResponse,
    summary="Get reading statistics and AI reflection for a given day",
)
@limiter.limit("10/minute")
async def get_reflection(
    request: Request,
    reflection_date: date,
    session: AsyncSession = Depends(get_db),
):
    """
    Rate-limited because each call runs a blocking Ollama generation. Without a
    limit, repeated requests queue LLM work on the same machine the pipeline
    runs on — a cheap way to starve it, especially over a public tunnel.
    """
    result = await session.execute(
        select(ReadingEventORM).where(ReadingEventORM.event_date == reflection_date)
    )
    events = result.scalars().all()

    if not events:
        raise HTTPException(
            status_code=404,
            detail=f"No reading events found for {reflection_date}.",
        )

    # Aggregate by topic
    topic_data: dict[str, dict] = defaultdict(lambda: {"times": [], "scrolls": []})
    for event in events:
        slug = event.topic_slug
        topic_data[slug]["times"].append(event.time_spent_seconds)
        topic_data[slug]["scrolls"].append(event.scroll_percent)

    topics = [
        TopicStat(
            topic_slug=slug,
            time_spent_seconds=sum(data["times"]),
            avg_scroll_percent=sum(data["scrolls"]) / len(data["scrolls"]),
            sessions=len(data["times"]),
        )
        for slug, data in topic_data.items()
    ]
    topics.sort(key=lambda t: -t.time_spent_seconds)

    total_time = sum(t.time_spent_seconds for t in topics)
    avg_scroll = sum(e.scroll_percent for e in events) / len(events)

    loop = asyncio.get_running_loop()
    ai_summary = await loop.run_in_executor(
        None, _generate_summary_sync, topics, total_time
    )

    return ReflectionResponse(
        date=reflection_date,
        total_reading_time_seconds=total_time,
        avg_scroll_depth=round(avg_scroll, 1),
        stories_read=len(topic_data),
        topics=topics,
        ai_summary=ai_summary,
    )
