"""
apps/api/routers/clusters.py
------------------------------
Story clusters CRUD — mounted under /api/graph/clusters by graph.py

Routes
------
  GET    /clusters                   list clusters (paginated)
  GET    /clusters/{cluster_id}      cluster detail with member articles
  PATCH  /clusters/{cluster_id}      rename topic_label
  DELETE /clusters/{cluster_id}      delete cluster (nullifies article FKs)
"""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.orm_models import ArticleORM, KnowledgeStoryORM, StoryClusterORM
from core.db.session import get_db

router = APIRouter()


class ClusterPatch(BaseModel):
    topic_label: Optional[str] = None


@router.get("", summary="List clusters")
async def list_clusters(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    since: Optional[date] = None,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(StoryClusterORM).order_by(StoryClusterORM.cluster_date.desc())
    if since:
        stmt = stmt.where(StoryClusterORM.cluster_date >= since)
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    res = await db.execute(stmt)
    rows = res.scalars().all()
    return [
        {
            "cluster_id": r.cluster_id,
            "topic_label": r.topic_label,
            "article_count": r.article_count,
            "cluster_date": str(r.cluster_date),
        }
        for r in rows
    ]


@router.get("/{cluster_id}", summary="Cluster detail with articles")
async def get_cluster(cluster_id: str, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(StoryClusterORM).where(StoryClusterORM.cluster_id == cluster_id)
    )
    row = res.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Cluster not found")
    art_res = await db.execute(
        select(ArticleORM).where(ArticleORM.cluster_id == cluster_id)
    )
    articles = art_res.scalars().all()
    return {
        "cluster_id": row.cluster_id,
        "topic_label": row.topic_label,
        "article_count": row.article_count,
        "cluster_date": str(row.cluster_date),
        "articles": [
            {
                "id": a.id, "title": a.title, "source": a.source,
                "url": a.url, "content_quality": a.content_quality,
            }
            for a in articles
        ],
    }


@router.patch("/{cluster_id}", summary="Rename cluster")
async def patch_cluster(
    cluster_id: str, body: ClusterPatch, db: AsyncSession = Depends(get_db)
):
    values = {k: v for k, v in body.model_dump().items() if v is not None}
    if not values:
        raise HTTPException(status_code=400, detail="No fields to update")
    result = await db.execute(
        update(StoryClusterORM)
        .where(StoryClusterORM.cluster_id == cluster_id)
        .values(**values)
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Cluster not found")
    await db.commit()
    return {"updated": cluster_id}


@router.delete("/{cluster_id}", summary="Delete cluster (cascades to stories)")
async def delete_cluster(cluster_id: str, db: AsyncSession = Depends(get_db)):
    # Nullify FK on articles before deleting the cluster
    await db.execute(
        update(ArticleORM)
        .where(ArticleORM.cluster_id == cluster_id)
        .values(cluster_id=None)
    )
    await db.execute(
        delete(KnowledgeStoryORM).where(KnowledgeStoryORM.cluster_id == cluster_id)
    )
    result = await db.execute(
        delete(StoryClusterORM).where(StoryClusterORM.cluster_id == cluster_id)
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Cluster not found")
    await db.commit()
    return {"deleted": cluster_id}
