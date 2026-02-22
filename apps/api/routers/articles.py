"""
apps/api/routers/articles.py
------------------------------
Articles CRUD — mounted under /api/graph/articles by graph.py

Routes
------
  GET    /articles             list articles (paginated, filterable)
  GET    /articles/{id}        single article detail
  PATCH  /articles/{id}        update title / content / source
  DELETE /articles/{id}        delete article
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.orm_models import ArticleORM
from core.db.session import get_db

router = APIRouter()


class ArticlePatch(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    source: Optional[str] = None


@router.get("", summary="List articles")
async def list_articles(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    source: Optional[str] = None,
    q: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(ArticleORM).order_by(ArticleORM.created_at.desc())
    if source:
        stmt = stmt.where(ArticleORM.source == source)
    if q:
        stmt = stmt.where(ArticleORM.title.ilike(f"%{q}%"))
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    res = await db.execute(stmt)
    rows = res.scalars().all()
    return [
        {
            "id": r.id,
            "title": r.title,
            "source": r.source,
            "url": r.url,
            "published_at": str(r.published_at) if r.published_at else None,
            "cluster_id": r.cluster_id,
            "content_quality": r.content_quality,
        }
        for r in rows
    ]


@router.get("/{article_id}", summary="Get article detail")
async def get_article(article_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(ArticleORM).where(ArticleORM.id == article_id))
    row = res.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Article not found")
    return {
        "id": row.id,
        "title": row.title,
        "source": row.source,
        "url": row.url,
        "content": row.content,
        "content_quality": row.content_quality,
        "published_at": str(row.published_at) if row.published_at else None,
        "cluster_id": row.cluster_id,
        "created_at": str(row.created_at),
    }


@router.patch("/{article_id}", summary="Update article")
async def patch_article(
    article_id: int, body: ArticlePatch, db: AsyncSession = Depends(get_db)
):
    values = {k: v for k, v in body.model_dump().items() if v is not None}
    if not values:
        raise HTTPException(status_code=400, detail="No fields to update")
    await db.execute(
        update(ArticleORM).where(ArticleORM.id == article_id).values(**values)
    )
    await db.commit()
    return {"updated": article_id}


@router.delete("/{article_id}", summary="Delete article")
async def delete_article(article_id: int, db: AsyncSession = Depends(get_db)):
    await db.execute(delete(ArticleORM).where(ArticleORM.id == article_id))
    await db.commit()
    return {"deleted": article_id}
