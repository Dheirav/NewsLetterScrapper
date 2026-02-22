"""
apps/api/routers/newsletter.py
--------------------------------
Routes:
  GET /api/newsletter/today        — redirect to today's date
  GET /api/newsletter/{date}       — return rendered HTML for the web reader
"""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.session import get_db
from services.newsletter.repository import get_newsletter_for_date

router = APIRouter()


@router.get("/today", response_class=RedirectResponse)
async def today_redirect():
    return RedirectResponse(url=f"/api/newsletter/{date.today().isoformat()}")


@router.get(
    "/{newsletter_date}",
    response_class=HTMLResponse,
    summary="Retrieve rendered newsletter HTML for a given date",
)
async def get_newsletter(
    newsletter_date: date,
    session: AsyncSession = Depends(get_db),
):
    orm = await get_newsletter_for_date(newsletter_date, session)
    if orm is None:
        raise HTTPException(
            status_code=404,
            detail=f"No newsletter found for {newsletter_date}. "
                   "Run the pipeline first: python scripts/run_pipeline.py",
        )
    return HTMLResponse(content=orm.html_content)
