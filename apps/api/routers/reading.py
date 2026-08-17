"""
apps/api/routers/reading.py
-----------------------------
Routes:
  POST /api/events/reading   — record a reading session event from the newsletter JS tracker
"""
import logging
from datetime import date
from typing import List

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.limiter import limiter
from core.db.orm_models import ReadingEventORM
from core.db.session import get_db

log = logging.getLogger(__name__)
router = APIRouter()


class ReadingEventIn(BaseModel):
    date: date
    topic_slug: str = Field(..., min_length=1, max_length=255)
    time_spent_seconds: int = Field(..., ge=0)
    scroll_percent: float = Field(..., ge=0.0, le=100.0)
    sections_read: List[str] = Field(default_factory=list)


@router.post(
    "/reading",
    status_code=status.HTTP_201_CREATED,
    summary="Record a newsletter reading event",
)
@limiter.limit("30/minute")
async def record_reading_event(
    request: Request,
    payload: ReadingEventIn,
    session: AsyncSession = Depends(get_db),
):
    """
    Called by the reading tracker script in the newsletter HTML.
    Stores how long the user spent on each story, how far they scrolled,
    and which sections they viewed.
    """
    event = ReadingEventORM(
        event_date=payload.date,
        topic_slug=payload.topic_slug,
        time_spent_seconds=payload.time_spent_seconds,
        scroll_percent=payload.scroll_percent,
        sections_read=payload.sections_read,
    )
    session.add(event)
    await session.flush()
    log.debug(
        "Reading event: topic=%s time=%ds scroll=%.0f%%",
        payload.topic_slug,
        payload.time_spent_seconds,
        payload.scroll_percent,
    )
    return {"id": event.id, "status": "recorded"}
