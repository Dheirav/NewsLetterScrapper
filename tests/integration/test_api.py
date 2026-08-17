"""
API routes against a real database.

The unit-level auth tests assert status codes ahead of any route logic. These
go through to the queries — which is where the CRUD endpoints were reporting
success for rows that never existed.
"""
from datetime import date, datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from apps.api.main import app
from core.db.session import get_db
from core.schemas.models import Article, Newsletter, StoryCluster
from services.ingestion.repository import save_articles
from services.newsletter.repository import save_newsletter
from services.understanding.repository import save_clusters

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


@pytest.fixture
def client(session, monkeypatch):
    """Route FastAPI's per-request session at the test transaction."""
    async def _override():
        yield session

    app.dependency_overrides[get_db] = _override
    monkeypatch.setattr("core.config.settings.api_key", "")
    yield AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    app.dependency_overrides.clear()


async def _seed_article(session, n=1):
    (saved,) = await save_articles([Article(
        title=f"Article {n}", source="Reuters", url=f"https://example.com/{n}",
        published_at=datetime.now(tz=timezone.utc), content="Body. " * 40,
    )], session)
    return saved


# ── Reads ────────────────────────────────────────────────────────────────────

async def test_newsletter_returns_stored_html(clean_tables, client):
    await save_newsletter(Newsletter(date(2026, 8, 17), [], "<h1>briefing</h1>"), clean_tables)
    resp = await client.get("/api/newsletter/2026-08-17")
    assert resp.status_code == 200
    assert "briefing" in resp.text


async def test_missing_newsletter_is_a_404_not_a_500(clean_tables, client):
    """Recipients click 'read online'; a 500 there is a bad experience."""
    resp = await client.get("/api/newsletter/2026-01-01")
    assert resp.status_code == 404
    assert "Run the pipeline" in resp.json()["detail"]


async def test_graph_stats_counts_real_rows(clean_tables, client):
    await _seed_article(clean_tables, 1)
    await _seed_article(clean_tables, 2)
    resp = await client.get("/api/graph/stats")
    assert resp.status_code == 200
    assert resp.json()["articles"] == 2


async def test_reading_event_is_persisted(clean_tables, client):
    resp = await client.post("/api/events/reading", json={
        "date": "2026-08-17", "topic_slug": "tariff-talks",
        "time_spent_seconds": 42, "scroll_percent": 80.0,
        "sections_read": ["context"],
    })
    assert resp.status_code == 201
    n = (await clean_tables.execute(text("SELECT count(*) FROM reading_events"))).scalar()
    assert n == 1


# ── Mutations must not lie about what they did ───────────────────────────────

async def test_patch_missing_article_is_404(clean_tables, client):
    resp = await client.patch("/api/graph/articles/999999", json={"title": "New"})
    assert resp.status_code == 404


async def test_delete_missing_article_is_404(clean_tables, client):
    resp = await client.delete("/api/graph/articles/999999")
    assert resp.status_code == 404


async def test_delete_missing_story_is_404(clean_tables, client):
    resp = await client.delete("/api/graph/stories/999999")
    assert resp.status_code == 404


async def test_delete_missing_cluster_is_404(clean_tables, client):
    resp = await client.delete("/api/graph/clusters/does-not-exist")
    assert resp.status_code == 404


async def test_patch_existing_article_actually_updates_it(clean_tables, client):
    article = await _seed_article(clean_tables)
    resp = await client.patch(f"/api/graph/articles/{article.id}",
                              json={"title": "Corrected headline"})
    assert resp.status_code == 200

    title = (await clean_tables.execute(
        text("SELECT title FROM articles WHERE id = :i"), {"i": article.id}
    )).scalar()
    assert title == "Corrected headline"


async def test_delete_existing_article_removes_the_row(clean_tables, client):
    article = await _seed_article(clean_tables)
    resp = await client.delete(f"/api/graph/articles/{article.id}")
    assert resp.status_code == 200

    n = (await clean_tables.execute(text("SELECT count(*) FROM articles"))).scalar()
    assert n == 0


async def test_deleting_a_cluster_detaches_its_articles(clean_tables, client):
    """The FK must be nulled first or the delete violates the constraint."""
    session = clean_tables
    article = await _seed_article(session)
    await save_clusters(
        [StoryCluster("cluster-a", "L", [article], datetime.now(tz=timezone.utc))],
        session, run_date=date(2026, 8, 17),
    )

    resp = await client.delete("/api/graph/clusters/cluster-a")
    assert resp.status_code == 200

    still_here = (await session.execute(text("SELECT count(*) FROM articles"))).scalar()
    orphaned = (await session.execute(
        text("SELECT count(*) FROM articles WHERE cluster_id IS NULL")
    )).scalar()
    assert still_here == 1, "articles outlive their cluster"
    assert orphaned == 1


# ── Unsubscribe end to end ───────────────────────────────────────────────────

async def test_unsubscribe_flow(clean_tables, client):
    from services.newsletter.unsubscribe import make_token

    email = "alice@example.com"
    resp = await client.get(
        f"/api/newsletter/unsubscribe?email={email}&token={make_token(email)}"
    )
    assert resp.status_code == 200
    assert "unsubscribed" in resp.text.lower()

    n = (await clean_tables.execute(text("SELECT count(*) FROM unsubscribes"))).scalar()
    assert n == 1


async def test_unsubscribe_rejects_a_forged_token(clean_tables, client):
    resp = await client.get(
        "/api/newsletter/unsubscribe?email=alice@example.com&token=forged"
    )
    assert resp.status_code == 400
    n = (await clean_tables.execute(text("SELECT count(*) FROM unsubscribes"))).scalar()
    assert n == 0


async def test_one_click_post_unsubscribes(clean_tables, client):
    """RFC 8058: mail clients POST here with no page load."""
    from services.newsletter.unsubscribe import make_token

    email = "bob@example.com"
    resp = await client.post(
        f"/api/newsletter/unsubscribe?email={email}&token={make_token(email)}"
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "unsubscribed"
