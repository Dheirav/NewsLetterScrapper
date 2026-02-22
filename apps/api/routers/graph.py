"""
apps/api/routers/graph.py
--------------------------
Obsidian-style knowledge graph explorer.

Routes served at /api/graph/:
  GET  /              serve graph.html explorer UI
  GET  /data          nodes + edges for the D3 force graph
  GET  /stats         overall DB statistics

  /articles/*   -> routers/articles.py
  /clusters/*   -> routers/clusters.py
  /stories/*    -> routers/stories.py
"""
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.orm_models import ArticleORM, KnowledgeStoryORM, NewsletterORM, StoryClusterORM
from core.db.session import get_db
from apps.api.routers.articles import router as articles_router
from apps.api.routers.clusters import router as clusters_router
from apps.api.routers.stories import router as stories_router

log = logging.getLogger(__name__)
GRAPH_HTML = Path(__file__).parents[3] / "templates" / "graph.html"

router = APIRouter()

# Mount CRUD sub-routers
router.include_router(articles_router, prefix="/articles", tags=["Graph Explorer"])
router.include_router(clusters_router, prefix="/clusters", tags=["Graph Explorer"])
router.include_router(stories_router,  prefix="/stories",  tags=["Graph Explorer"])


# -- UI -----------------------------------------------------------------------

@router.get("/", include_in_schema=False)
async def graph_ui() -> FileResponse:
    from fastapi import HTTPException
    if not GRAPH_HTML.exists():
        raise HTTPException(status_code=404, detail="graph.html not found")
    return FileResponse(GRAPH_HTML, media_type="text/html")


# -- Graph data (nodes + edges) -----------------------------------------------

@router.get("/data", summary="Nodes and edges for the knowledge graph")
async def graph_data(
    days: int = Query(7, ge=1, le=90, description="How many past days to include"),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """
    Returns a graph payload ready for D3 force simulation:
      { "nodes": [...], "edges": [...] }

    Node types: article | cluster | story | newsletter
    Edge types: member (article->cluster) | knowledge (cluster->story)
    """
    since = date.today() - timedelta(days=days)
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []

    clusters_res = await db.execute(
        select(StoryClusterORM).where(StoryClusterORM.cluster_date >= since)
    )
    clusters = clusters_res.scalars().all()
    cluster_ids = {c.cluster_id for c in clusters}

    for c in clusters:
        nodes.append({
            "id": f"cluster_{c.cluster_id}", "type": "cluster",
            "label": c.topic_label[:60],
            "data": {
                "cluster_id": c.cluster_id, "topic_label": c.topic_label,
                "article_count": c.article_count, "cluster_date": str(c.cluster_date),
            },
        })

    if cluster_ids:
        articles_res = await db.execute(
            select(ArticleORM).where(ArticleORM.cluster_id.in_(cluster_ids))
        )
        for a in articles_res.scalars().all():
            nodes.append({
                "id": f"article_{a.id}", "type": "article", "label": a.title[:60],
                "data": {
                    "id": a.id, "title": a.title, "source": a.source, "url": a.url,
                    "published_at": str(a.published_at) if a.published_at else None,
                    "cluster_id": a.cluster_id,
                    "content_quality": a.content_quality,
                },
            })
            if a.cluster_id:
                edges.append({
                    "source": f"article_{a.id}",
                    "target": f"cluster_{a.cluster_id}",
                    "type": "member",
                })

        stories_res = await db.execute(
            select(KnowledgeStoryORM).where(KnowledgeStoryORM.cluster_id.in_(cluster_ids))
        )
        for s in stories_res.scalars().all():
            nodes.append({
                "id": f"story_{s.id}", "type": "story", "label": s.topic_label[:60],
                "data": {
                    "id": s.id, "cluster_id": s.cluster_id,
                    "topic_label": s.topic_label,
                    "executive_summary": s.executive_summary[:200],
                    "source_count": s.source_count, "story_date": str(s.story_date),
                },
            })
            edges.append({
                "source": f"cluster_{s.cluster_id}",
                "target": f"story_{s.id}",
                "type": "knowledge",
            })

    newsletters_res = await db.execute(
        select(NewsletterORM).where(NewsletterORM.newsletter_date >= since)
    )
    for n in newsletters_res.scalars().all():
        nodes.append({
            "id": f"newsletter_{n.id}", "type": "newsletter",
            "label": str(n.newsletter_date),
            "data": {
                "id": n.id, "newsletter_date": str(n.newsletter_date),
                "sent": n.sent, "sent_at": str(n.sent_at) if n.sent_at else None,
            },
        })

    return JSONResponse({"nodes": nodes, "edges": edges})


# -- Stats --------------------------------------------------------------------

@router.get("/stats", summary="Overall database statistics")
async def db_stats(db: AsyncSession = Depends(get_db)):
    art_res  = await db.execute(select(func.count(ArticleORM.id)))
    cl_res   = await db.execute(select(func.count(StoryClusterORM.cluster_id)))
    ks_res   = await db.execute(select(func.count(KnowledgeStoryORM.id)))
    nl_res   = await db.execute(select(func.count(NewsletterORM.id)))
    return {
        "articles":         art_res.scalar(),
        "clusters":         cl_res.scalar(),
        "knowledge_stories": ks_res.scalar(),
        "newsletters":      nl_res.scalar(),
    }
