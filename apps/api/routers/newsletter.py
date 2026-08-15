"""
apps/api/routers/newsletter.py
--------------------------------
Routes:
  GET /api/newsletter/today                    — redirect to today's date
  GET /api/newsletter/{date}                   — return rendered HTML for web reader
  GET /api/newsletter/unsubscribe?email=&token= — one-click email opt-out
"""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.session import get_db
from services.newsletter.repository import get_newsletter_for_date
from services.newsletter.unsubscribe import opt_out, verify_token, get_all_opted_out

router = APIRouter()


@router.get("/today", response_class=RedirectResponse)
async def today_redirect():
    return RedirectResponse(url=f"/api/newsletter/{date.today().isoformat()}")


@router.get(
    "/unsubscribe",
    response_class=HTMLResponse,
    summary="One-click email unsubscribe",
)
async def unsubscribe(
    email: str = Query(...),
    token: str = Query(...),
    session: AsyncSession = Depends(get_db),
):
    if not verify_token(email, token):
        raise HTTPException(status_code=400, detail="Invalid unsubscribe token.")
    newly_added = await opt_out(email, session)
    heading = "You've been unsubscribed" if newly_added else "Already unsubscribed"
    return HTMLResponse(content=f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8" /><title>Unsubscribed</title>
<style>body{{font-family:sans-serif;max-width:480px;margin:5rem auto;text-align:center;color:#1a1a1a}}
h2{{font-size:1.4rem;margin-bottom:1rem}}p{{color:#666}}</style></head>
<body>
  <h2>&#10003; {heading}</h2>
  <p><strong>{email}</strong> will no longer receive the Intelligence Briefing.</p>
</body></html>""")


@router.get(
    "/opted-out",
    summary="List all opted-out email addresses",
    response_model=list[str],
)
async def list_opted_out(session: AsyncSession = Depends(get_db)):
    """Return every email address that has unsubscribed, sorted alphabetically."""
    return await get_all_opted_out(session)


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
