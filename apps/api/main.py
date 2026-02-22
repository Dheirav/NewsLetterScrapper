"""
apps/api/main.py
-----------------
FastAPI application entry point.

Start with:
    uvicorn apps.api.main:app --reload --host 0.0.0.0 --port 8000
"""
import logging
import logging.config
from pathlib import Path

from fastapi import FastAPI, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

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

# ── Rate limiter (slowapi) ────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address)

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

# ── API key authentication middleware ─────────────────────────────────────────
_UNPROTECTED_PREFIXES = ("/health",)
_UNPROTECTED_EXACT = ("/",)


@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    """
    Enforce X-API-Key on every route except /health.
    Disabled (no-op) when API_KEY env var is empty — safe for local dev.
    """
    if not settings.api_key:
        return await call_next(request)
    if any(request.url.path.startswith(p) for p in _UNPROTECTED_PREFIXES):
        return await call_next(request)
    if request.url.path in _UNPROTECTED_EXACT:
        return await call_next(request)
    if request.method == "OPTIONS":
        return await call_next(request)
    key = request.headers.get("X-API-Key", "")
    if key != settings.api_key:
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid or missing X-API-Key"},
        )
    return await call_next(request)


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
