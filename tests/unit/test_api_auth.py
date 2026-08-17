"""
Tests for the API key allowlist.

Two regressions are guarded here:

1. With API_KEY unset, every route — including DELETE on articles, clusters and
   stories — was reachable by anyone. The README instructs the user to expose
   port 8000 through ngrok or Cloudflare Tunnel, so "unset" meant "public".

2. With API_KEY set, the old middleware required an X-API-Key header on
   everything except /health. The dashboard, graph explorer and reflection
   views are reached by plain <a href> clicks and the reading tracker posts
   from page JS — none of which can send a custom header. Turning the key on
   therefore broke the product, so it was never turned on.

Auth is enforced in middleware ahead of any route logic, so these assertions
need no database. Public routes are asserted as "not 401" rather than "200"
because most of them do hit the DB.
"""
import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from core.config import settings

KEY = "test-key-do-not-use"

# Reachable by a recipient's browser with no credential.
PUBLIC = [
    "/",
    "/health",
    "/api/newsletter/today",
    "/api/newsletter/2026-08-17",
    "/api/newsletter/unsubscribe?email=a@b.com&token=x",
]

# Destructive, private, or expensive.
PROTECTED = [
    "/api/graph/",
    "/api/graph/data",
    "/api/graph/stats",
    "/api/graph/articles",
    "/api/graph/clusters",
    "/api/graph/stories",
    "/api/newsletter/opted-out",   # returns every subscriber's address
    "/api/reflection/today",
    "/api/reflection/2026-08-17",
    "/docs",                       # advertises the DELETE surface
    "/openapi.json",
]


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def locked(monkeypatch):
    """Run with authentication switched on."""
    monkeypatch.setattr(settings, "api_key", KEY)


@pytest.fixture
def unlocked(monkeypatch):
    """Run with authentication disabled, as in local development."""
    monkeypatch.setattr(settings, "api_key", "")


# ── Key disabled: nothing is challenged ──────────────────────────────────────

@pytest.mark.parametrize("path", PUBLIC + PROTECTED)
def test_no_key_configured_challenges_nothing(client, unlocked, path):
    assert client.get(path).status_code != 401


# ── Key enabled: the public set stays open ───────────────────────────────────

@pytest.mark.parametrize("path", PUBLIC)
def test_public_paths_never_require_a_key(client, locked, path):
    assert client.get(path).status_code != 401


def test_reading_tracker_can_post_without_a_key(client, locked):
    """Page JS has no way to obtain the key; a 401 here silently ends learning."""
    resp = client.post("/api/events/reading", json={
        "date": "2026-08-17", "topic_slug": "x",
        "time_spent_seconds": 5, "scroll_percent": 50.0, "sections_read": [],
    })
    assert resp.status_code != 401


# ── Key enabled: everything else is challenged ───────────────────────────────

@pytest.mark.parametrize("path", PROTECTED)
def test_protected_paths_reject_missing_key(client, locked, path):
    assert client.get(path, headers={"accept": "application/json"}).status_code == 401


@pytest.mark.parametrize("path", PROTECTED)
def test_protected_paths_reject_wrong_key(client, locked, path):
    resp = client.get(path, headers={"X-API-Key": "wrong", "accept": "application/json"})
    assert resp.status_code == 401


@pytest.mark.parametrize("path", PROTECTED)
def test_protected_paths_accept_header_key(client, locked, path):
    assert client.get(path, headers={"X-API-Key": KEY}).status_code != 401


@pytest.mark.parametrize("path", PROTECTED)
def test_protected_paths_accept_cookie(client, locked, path):
    """Browser navigation cannot send a header, so the cookie must work."""
    client.cookies.set("briefing_key", KEY)
    assert client.get(path).status_code != 401


def test_delete_endpoints_are_protected(client, locked):
    for path in ("/api/graph/articles/1", "/api/graph/stories/1", "/api/graph/clusters/abc"):
        resp = client.delete(path, headers={"accept": "application/json"})
        assert resp.status_code == 401, f"{path} was not protected"


def test_opted_out_roster_is_not_public(client, locked):
    """It sits under the otherwise-public /api/newsletter prefix."""
    resp = client.get("/api/newsletter/opted-out", headers={"accept": "application/json"})
    assert resp.status_code == 401


# ── Browser flow ─────────────────────────────────────────────────────────────

def test_browser_navigation_redirects_to_unlock(client, locked):
    resp = client.get("/api/graph/", headers={"accept": "text/html"}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/unlock?next=")


def test_unlock_sets_cookie_for_correct_key(client, locked):
    resp = client.get(f"/unlock?key={KEY}&next=/api/graph/", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/api/graph/"
    assert "briefing_key" in resp.cookies


def test_unlock_rejects_wrong_key(client, locked):
    resp = client.get("/unlock?key=nope", follow_redirects=False)
    assert resp.status_code == 401
    assert "briefing_key" not in resp.cookies


def test_unlock_form_is_reachable_without_a_key(client, locked):
    resp = client.get("/unlock", follow_redirects=False)
    assert resp.status_code == 200
    assert "API key" in resp.text


# ── Default-deny posture ─────────────────────────────────────────────────────

def test_unknown_paths_are_protected_by_default(client, locked):
    """A route added later must be private until deliberately published."""
    resp = client.get("/api/some/future/route", headers={"accept": "application/json"})
    assert resp.status_code == 401


# ── CLI entrypoints must not run under production validation ─────────────────

def test_cli_scripts_do_not_fake_app_env():
    """
    explore_db.py and crud_db.py used to set APP_ENV=production purely to
    silence SQLAlchemy echo. That put read-only tools under production
    validation, so adding any production-only check broke them at import. They
    use SQL_ECHO now; keep it that way.
    """
    import pathlib
    for name in ("explore_db.py", "crud_db.py", "archive.py", "send_newsletter.py"):
        src = (pathlib.Path("scripts") / name).read_text()
        assert 'APP_ENV", "production"' not in src, (
            f"{name} fakes APP_ENV=production; use SQL_ECHO=false instead"
        )


def test_sql_echo_is_independent_of_app_env():
    from core.config import Settings
    assert Settings(app_env="development").sql_echo is True
    assert Settings(app_env="development", sql_echo=False).sql_echo is False
