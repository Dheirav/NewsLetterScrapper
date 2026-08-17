"""Proves the fixture isolates correctly before anything else relies on it."""
import pytest
from sqlalchemy import text

# Must share the session-scoped engine's event loop.
pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def test_running_inside_a_throwaway_schema(session):
    schema = (await session.execute(text("SELECT current_schema()"))).scalar()
    assert schema.startswith("test_")


async def test_tables_exist_and_are_empty(session):
    for table in ("articles", "story_clusters", "knowledge_stories", "newsletters"):
        n = (await session.execute(text(f"SELECT count(*) FROM {table}"))).scalar()
        assert n == 0, f"{table} should start empty, saw {n}"


async def test_live_data_is_not_visible(session):
    """The real articles table holds ~14k rows; we must not be looking at it."""
    n = (await session.execute(text("SELECT count(*) FROM articles"))).scalar()
    assert n == 0


async def test_pgvector_is_available(session):
    v = (await session.execute(
        text("SELECT extversion FROM pg_extension WHERE extname='vector'")
    )).scalar()
    assert v is not None


async def test_global_get_session_cannot_reach_the_real_database(session):
    """
    The regression that cost 14,097 rows.

    Application code calling get_session() itself used to open a fresh
    connection from the global engine, bypassing the test schema entirely.
    scripts/archive.py did exactly that, so an integration test running the
    archive deleted from the production schema. The autouse fixture in
    conftest redirects every such entry point at the test transaction.
    """
    from core.db.session import get_session

    async with get_session() as s:
        schema = (await s.execute(text("SELECT current_schema()"))).scalar()
        assert schema.startswith("test_"), (
            f"get_session() escaped to {schema!r} — it must stay in the test schema"
        )
        n = (await s.execute(text("SELECT count(*) FROM articles"))).scalar()
        assert n == 0, "get_session() is seeing live data"


async def test_archive_module_get_session_is_redirected(session):
    """`from ... import get_session` binds a separate reference per module."""
    import scripts.archive as arch

    async with arch.get_session() as s:
        schema = (await s.execute(text("SELECT current_schema()"))).scalar()
        assert schema.startswith("test_")
