"""
apps/api/main.py
-----------------
FastAPI application entry point.

Start with:
    uvicorn apps.api.main:app --reload --host 0.0.0.0 --port 8000
"""
import hmac
import logging
import logging.config
import re
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
)
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.limiter import limiter
from apps.api.routers import newsletter, reading, reflection, graph
from core.config import settings
from core.db.session import get_db

# ── Logging setup ─────────────────────────────────────────────────────────────
def _build_logging_config() -> dict:
    if settings.log_format == "json":
        formatter: dict = {
            "()": "pythonjsonlogger.jsonlogger.JsonFormatter",
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
        }
    else:
        formatter = {
            "format": "%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
            "datefmt": "%H:%M:%S",
        }
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"default": formatter},
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "default",
            }
        },
        "root": {
            "level": settings.log_level.upper(),
            "handlers": ["console"],
        },
        "loggers": {
            "uvicorn.access": {"level": "WARNING"},
            "sqlalchemy.engine": {"level": "WARNING"},
            "httpx": {"level": "WARNING"},
            "httpcore": {"level": "WARNING"},
        },
    }


logging.config.dictConfig(_build_logging_config())

app = FastAPI(
    title="Intelligence Briefing API",
    description="Personal daily intelligence briefing — newsletter, reading tracker, and reflection",
    version="1.0.0",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-API-Key"],
)

# ── API key authentication ────────────────────────────────────────────────────
#
# Default-deny: anything not explicitly listed below requires the key. A route
# added later is therefore protected until someone deliberately publishes it,
# which is the opposite of the previous "exempt a few prefixes" posture.
#
# The public set is exactly what a recipient's browser must reach without any
# credential: reading the newsletter, opting out, and posting reading events.
# Everything else — the whole /api/graph CRUD surface, the reflection endpoint,
# the opted-out roster, and the API docs that advertise them — needs the key.
#
# Note /api/newsletter/opted-out is deliberately NOT public: it returns every
# subscriber's email address.

_PUBLIC_EXACT: frozenset[str] = frozenset({
    "/",                                # dashboard shell
    "/health",                          # liveness probe / uptime monitors
    "/api/newsletter/today",
    "/api/newsletter/unsubscribe",      # GET (confirmation page) and POST (RFC 8058)
    "/api/events/reading",              # browser tracker; cannot send a header
    "/unlock",                          # how a browser obtains the cookie
})

# /api/newsletter/2026-08-17 — public. Matched by shape so that sibling routes
# such as /api/newsletter/opted-out do not fall through into the public set.
_PUBLIC_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^/api/newsletter/\d{4}-\d{2}-\d{2}/?$"),
)

_COOKIE_NAME = "briefing_key"


def _is_public(path: str) -> bool:
    if path in _PUBLIC_EXACT:
        return True
    return any(p.match(path) for p in _PUBLIC_PATTERNS)


def _presented_key(request: Request) -> str:
    """Accept the key from a header (scripts) or a cookie (browser navigation).

    The cookie matters because the dashboard and graph explorer are reached by
    plain <a href> clicks, and a link cannot carry a custom header.
    """
    return request.headers.get("X-API-Key") or request.cookies.get(_COOKIE_NAME, "")


@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    """
    Enforce the API key on everything outside the public allowlist.
    Disabled (no-op) when API_KEY is empty — keeps local development friction-free.
    """
    if not settings.api_key:
        return await call_next(request)
    if request.method == "OPTIONS" or _is_public(request.url.path):
        return await call_next(request)

    if not hmac.compare_digest(_presented_key(request), settings.api_key):
        # A browser navigation deserves a page it can act on; a script deserves JSON.
        if "text/html" in request.headers.get("accept", ""):
            return RedirectResponse(url=f"/unlock?next={quote(request.url.path)}", status_code=303)
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid or missing X-API-Key"},
        )
    return await call_next(request)


@app.get("/unlock", include_in_schema=False)
async def unlock(key: str = "", next: str = "/"):
    """
    Exchange the API key for a cookie so browser navigation works through a
    tunnel. Without this, setting API_KEY makes the dashboard, graph explorer
    and reflection views unreachable from a browser — they are ordinary links.
    """
    if not settings.api_key:
        return RedirectResponse(url=next or "/", status_code=303)

    if key and hmac.compare_digest(key, settings.api_key):
        response = RedirectResponse(url=next or "/", status_code=303)
        response.set_cookie(
            _COOKIE_NAME,
            key,
            httponly=True,
            samesite="lax",
            secure=settings.public_url.startswith("https://"),
            max_age=60 * 60 * 24 * 30,
        )
        return response

    return HTMLResponse(
        status_code=200 if not key else 401,
        content=f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8" /><title>Unlock</title>
<style>body{{font-family:system-ui,sans-serif;max-width:24rem;margin:6rem auto;padding:0 1rem;color:#1a1a1a}}
h1{{font-size:1.2rem;margin-bottom:.5rem}}p{{color:#666;font-size:.9rem}}
input,button{{font:inherit;width:100%;padding:.6rem;margin-top:.6rem;box-sizing:border-box}}
.err{{color:#b3261e;font-size:.85rem}}</style></head>
<body>
  <h1>Intelligence Briefing</h1>
  <p>This view is private. Enter the API key to continue.</p>
  {'<p class="err">That key was not accepted.</p>' if key else ''}
  <form method="get" action="/unlock">
    <input type="hidden" name="next" value="{quote(next or '/')}" />
    <input type="password" name="key" placeholder="API key" autofocus />
    <button type="submit">Unlock</button>
  </form>
</body></html>""",
    )


# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(newsletter.router, prefix="/api/newsletter", tags=["Newsletter"])
app.include_router(reading.router,    prefix="/api/events",     tags=["Reading Tracker"])
app.include_router(reflection.router, prefix="/api/reflection", tags=["Reflection"])
app.include_router(graph.router,      prefix="/api/graph",      tags=["Graph Explorer"])


@app.get("/", include_in_schema=False)
async def dashboard():
    """Serve the HTML navigation dashboard at the root URL."""
    return FileResponse(Path(__file__).parent.parent.parent / "templates" / "dashboard.html")


@app.get("/health", tags=["Health"])
async def health_check(session: AsyncSession = Depends(get_db)):
    """
    Liveness + readiness check.
    Returns 200 only when the API process AND the database are reachable.
    Any monitoring tool (UptimeRobot, Docker HEALTHCHECK, etc.) should poll this.
    """
    await session.execute(text("SELECT 1"))
    return {"status": "ok", "db": "ok"}
