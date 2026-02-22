"""
scripts/init_db.py
------------------
One-time setup script.  Run this before using the system for the first time:

    python scripts/init_db.py

What it does:
1. Connects to PostgreSQL as the configured user
2. Enables the pgvector extension
3. Applies all Alembic migrations (creates all tables)
4. Seeds an empty UserProfile row (id=1) if not already present
"""
import asyncio
import logging
import subprocess
import sys
from pathlib import Path

# make sure project root is on sys.path when run directly
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from core.config import settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)


async def enable_pgvector() -> None:
    engine = create_async_engine(settings.database_url, echo=False)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            log.info("pgvector extension enabled (or already present)")
    except Exception as exc:
        if "permission denied" in str(exc).lower() or "superuser" in str(exc).lower():
            log.warning(
                "Cannot create pgvector extension as this user — ensure it was already "
                "enabled by a superuser:  sudo -u postgres psql -d <db> -c "
                "\"CREATE EXTENSION IF NOT EXISTS vector;\"\n  Continuing …"
            )
        else:
            raise
    finally:
        await engine.dispose()


def run_alembic_upgrade() -> None:
    log.info("Running Alembic migrations …")
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        capture_output=True,
        text=True,
        cwd=str(_PROJECT_ROOT),
    )
    if result.returncode != 0:
        log.error("Alembic failed:\n%s", result.stderr)
        raise RuntimeError("Migration failed")
    log.info(result.stdout.strip() or "Migrations applied successfully")


async def seed_user_profile() -> None:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from sqlalchemy import select
    from core.db.orm_models import UserProfileORM

    engine = create_async_engine(settings.database_url, echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        existing = await session.get(UserProfileORM, 1)
        if not existing:
            session.add(UserProfileORM(id=1))
            await session.commit()
            log.info("Seeded empty UserProfile (id=1)")
        else:
            log.info("UserProfile already exists — skipped seed")
    await engine.dispose()


async def main() -> None:
    log.info("=== Intelligence Briefing — Database Initialisation ===")
    await enable_pgvector()
    run_alembic_upgrade()
    await seed_user_profile()
    log.info("=== Database ready ===")


if __name__ == "__main__":
    asyncio.run(main())
