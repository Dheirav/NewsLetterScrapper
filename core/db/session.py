"""
Async SQLAlchemy engine and session factory.

Usage (in services / scripts):
    async with get_session() as session:
        result = await session.execute(select(ArticleORM))

Usage (in FastAPI dependency injection):
    async def my_route(session: AsyncSession = Depends(get_db)):
        ...
"""
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from core.config import settings

_engine = create_async_engine(
    settings.database_url,
    # SQL_ECHO, defaulting to on in development. Kept separate from APP_ENV so a
    # CLI can ask for quiet output without claiming to be a production process.
    echo=bool(settings.sql_echo),
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)

_SessionFactory = async_sessionmaker(
    bind=_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Context-manager session for use in services and scripts."""
    async with _SessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a session per request."""
    async with _SessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def get_engine():
    return _engine
