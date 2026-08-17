"""
Database fixtures for integration tests.

Everything here runs against real PostgreSQL with real pgvector. Unit tests
stub the session, which is why bugs living in the SQL itself — a predicate that
never matches, an upsert that silently no-ops, a 404 that reports success —
survived until now.

Isolation strategy
------------------
Tests run inside a throwaway schema rather than a separate database, because
the project's database role has no CREATEDB privilege. The schema is created
per session, every connection gets `search_path=<schema>,public` (public is
needed for the pgvector `vector` type, which the extension owns), and the
schema is dropped afterwards.

Set TEST_DATABASE_URL to point somewhere else entirely if you prefer.

Safety
------
Schema isolation means a mistake in search_path would land DML on live tables,
so `_assert_isolated` refuses to proceed unless the tables it can see are
empty. On a developer machine with real data that check fails loudly instead of
deleting anything.
"""
import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.config import settings
from core.db.base import Base
# Imported for the side effect of registering every table on Base.metadata.
from core.db import orm_models  # noqa: F401

TEST_SCHEMA = f"test_{uuid.uuid4().hex[:12]}"

DATABASE_URL = os.environ.get("TEST_DATABASE_URL", settings.database_url)


def _skip_reason() -> str | None:
    if os.environ.get("SKIP_INTEGRATION_TESTS"):
        return "SKIP_INTEGRATION_TESTS is set"
    return None


@pytest_asyncio.fixture(scope="session")
async def engine():
    reason = _skip_reason()
    if reason:
        pytest.skip(reason)

    eng = create_async_engine(
        DATABASE_URL,
        echo=False,
        poolclass=None,
        connect_args={"server_settings": {"search_path": f"{TEST_SCHEMA},public"}},
    )

    try:
        async with eng.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{TEST_SCHEMA}"'))
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    except Exception as exc:  # no database, no permissions, no pgvector
        await eng.dispose()
        pytest.skip(f"integration database unavailable: {str(exc)[:120]}")

    # checkfirst=False is essential. With it enabled SQLAlchemy resolves each
    # table through the search_path, finds the real public.articles, decides
    # there is nothing to create — and every query then silently addresses live
    # data. Creating unconditionally puts the tables in the first schema on the
    # path, which is the throwaway one. The schema is fresh per session, so
    # there is nothing to collide with.
    async with eng.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, checkfirst=False))

    await _assert_isolated(eng)

    yield eng

    async with eng.begin() as conn:
        await conn.execute(text(f'DROP SCHEMA IF EXISTS "{TEST_SCHEMA}" CASCADE'))
    await eng.dispose()


async def _assert_isolated(eng) -> None:
    """
    Fail loudly rather than operate on live data.

    If search_path were wrong these queries would resolve to the real tables,
    which on a working install hold tens of thousands of rows.
    """
    async with eng.connect() as conn:
        current = (await conn.execute(text("SELECT current_schema()"))).scalar()
        if current != TEST_SCHEMA:
            pytest.fail(
                f"expected to be isolated in {TEST_SCHEMA}, but current_schema() "
                f"is {current!r} — refusing to run against live tables"
            )
        for table in ("articles", "story_clusters", "knowledge_stories", "newsletters"):
            count = (await conn.execute(text(f"SELECT count(*) FROM {table}"))).scalar()
            if count:
                pytest.fail(
                    f"{table} already holds {count} rows inside {TEST_SCHEMA}; "
                    "isolation is not working, refusing to run"
                )


@pytest_asyncio.fixture
async def session(engine) -> AsyncSession:
    """
    A session per test, rolled back afterwards.

    The outer transaction is never committed, so `session.commit()` inside
    application code resolves to a savepoint release and the test still leaves
    no trace.
    """
    async with engine.connect() as conn:
        trans = await conn.begin()
        factory = async_sessionmaker(
            bind=conn, class_=AsyncSession, expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        async with factory() as s:
            yield s
        await trans.rollback()


@pytest_asyncio.fixture(autouse=True)
async def _no_escape_to_the_real_database(session, monkeypatch):
    """
    Redirect every global session entry point at the test transaction.

    Schema isolation only covers sessions a test creates. Application code that
    calls ``get_session()`` itself opens a fresh connection from the global
    engine, which has no test search_path — so it operates on live tables. That
    is not hypothetical: it is how an early version of these tests deleted
    14,097 real articles by running the archive against the production schema.

    Every module that imported ``get_session`` gets its reference replaced, not
    just the definition in core.db.session, because `from ... import` binds the
    function into the importing module's namespace.
    """
    import sys
    from contextlib import asynccontextmanager

    import core.db.session as db_session

    @asynccontextmanager
    async def _test_session():
        # Yield the test's own session; the surrounding transaction is rolled
        # back by the `session` fixture, so callers cannot commit anything.
        yield session

    monkeypatch.setattr(db_session, "get_session", _test_session)
    for module in list(sys.modules.values()):
        if module and getattr(module, "__name__", "").startswith(
            ("services.", "scripts.", "apps.")
        ):
            if getattr(module, "get_session", None) is not None:
                monkeypatch.setattr(module, "get_session", _test_session, raising=False)


@pytest_asyncio.fixture
async def clean_tables(session):
    """Truncate between tests that need a guaranteed-empty starting point."""
    await session.execute(text(
        "TRUNCATE articles, story_clusters, knowledge_stories, newsletters, "
        "reading_events, user_profiles, pipeline_runs, failed_generations, "
        "unsubscribes RESTART IDENTITY CASCADE"
    ))
    await session.flush()
    return session
