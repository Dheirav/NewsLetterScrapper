"""
apps/api/routers/stories.py
-----------------------------
Knowledge stories CRUD — mounted under /api/graph/stories by graph.py

Routes
------
  GET    /stories           list knowledge stories (paginated)
  GET    /stories/{id}      full knowledge story detail
  PATCH  /stories/{id}      update any story field
  DELETE /stories/{id}      delete story
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.orm_models import KnowledgeStoryORM
from core.db.session import get_db

router = APIRouter()


class StoryPatch(BaseModel):
    topic_label: Optional[str] = None
    executive_summary: Optional[str] = None
    context: Optional[str] = None
    why_it_matters: Optional[str] = None
    implications: Optional[str] = None
    talking_points: Optional[List[str]] = None


@router.get("", summary="List knowledge stories")
async def list_stories(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(KnowledgeStoryORM)
        .order_by(KnowledgeStoryORM.story_date.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    res = await db.execute(stmt)
    rows = res.scalars().all()
    return [
        {
            "id": r.id,
            "cluster_id": r.cluster_id,
            "topic_label": r.topic_label,
            "executive_summary": r.executive_summary[:200],
            "source_count": r.source_count,
            "story_date": str(r.story_date),
        }
        for r in rows
    ]


@router.get("/{story_id}", summary="Full knowledge story detail")
async def get_story(story_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(KnowledgeStoryORM).where(KnowledgeStoryORM.id == story_id)
    )
    row = res.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Story not found")
    return {
        "id": row.id,
        "cluster_id": row.cluster_id,
        "topic_label": row.topic_label,
        "executive_summary": row.executive_summary,
        "context": row.context,
        "why_it_matters": row.why_it_matters,
        "implications": row.implications,
        "talking_points": row.talking_points or [],
        "reliability_notes": row.reliability_notes,
        "source_count": row.source_count,
        "article_urls": row.article_urls or [],
        "article_sources": row.article_sources or [],
        "story_date": str(row.story_date),
    }


@router.patch("/{story_id}", summary="Update knowledge story")
async def patch_story(
    story_id: int, body: StoryPatch, db: AsyncSession = Depends(get_db)
):
    raw = body.model_dump()
    values: Dict[str, Any] = {k: v for k, v in raw.items() if v is not None}
    if not values:
        raise HTTPException(status_code=400, detail="No fields to update")
    await db.execute(
        update(KnowledgeStoryORM)
        .where(KnowledgeStoryORM.id == story_id)
        .values(**values)
    )
    await db.commit()
    return {"updated": story_id}


@router.delete("/{story_id}", summary="Delete knowledge story")
async def delete_story(story_id: int, db: AsyncSession = Depends(get_db)):
    await db.execute(
        delete(KnowledgeStoryORM).where(KnowledgeStoryORM.id == story_id)
    )
    await db.commit()
    return {"deleted": story_id}
