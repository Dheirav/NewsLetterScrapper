"""
Tests for unsubscribe link construction and List-Unsubscribe headers.

The regression guarded here: PUBLIC_URL defaults to http://localhost:8000, and
that value was baked into the unsubscribe link and the "read online" link of
every email actually sent. Recipients received links only the sending machine
could resolve, and nothing surfaced the failure.

The mailto fallback exists because a self-hosted pipeline is asleep most of the
time — an HTTPS unsubscribe endpoint on a laptop is not reachable when someone
opens the email two days later.
"""
import re
from datetime import date

import pytest

from core.config import Settings
from core.schemas.models import KnowledgeStory, Newsletter


def _settings(public_url: str) -> Settings:
    return Settings(public_url=public_url, smtp_user="briefing@example.com")


def _newsletter() -> Newsletter:
    story = KnowledgeStory(
        cluster_id="c1", topic_label="Tariff talks stall",
        executive_summary="S.", context="C.", why_it_matters="M.",
        implications="I.", talking_points=["a"], source_count=3,
        article_urls=["https://example.com/a"], article_sources=["Reuters"],
    )
    return Newsletter(date=date(2026, 8, 17), stories=[story])


def _build(monkeypatch, public_url: str):
    """Render + build a message under a patched PUBLIC_URL."""
    patched = _settings(public_url)
    import core.config
    import services.newsletter.emailer as emailer
    import services.newsletter.renderer as renderer
    import services.newsletter.unsubscribe as unsub
    for module in (core.config, emailer, renderer, unsub):
        monkeypatch.setattr(module, "settings", patched)

    html = renderer.render_email_html(_newsletter())
    msg = emailer._build_message(html, "Briefing", "alice@example.com")
    body = msg.get_payload()[1].get_payload(decode=True).decode()
    return msg, body


def _footer_href(body: str) -> str:
    match = re.search(r'<a href="([^"]+)"[^>]*>Unsubscribe</a>', body)
    assert match, "footer unsubscribe link missing from rendered email"
    return match.group(1)


# ── Reachability classification ──────────────────────────────────────────────

@pytest.mark.parametrize(
    "url", ["http://localhost:8000", "http://127.0.0.1:8000", "http://0.0.0.0:8000"]
)
def test_loopback_urls_are_not_reachable(url):
    assert _settings(url).public_url_is_reachable is False


@pytest.mark.parametrize(
    "url", ["https://briefing.fly.dev", "http://192.168.1.50:8000"]
)
def test_routable_urls_are_reachable(url):
    assert _settings(url).public_url_is_reachable is True


def test_one_click_requires_https():
    """RFC 8058 one-click is HTTPS-only; plain http must not advertise it."""
    assert _settings("https://briefing.fly.dev").supports_one_click_unsubscribe is True
    assert _settings("http://192.168.1.50:8000").supports_one_click_unsubscribe is False
    assert _settings("http://localhost:8000").supports_one_click_unsubscribe is False


# ── The localhost case: what the project ships with today ────────────────────

def test_localhost_never_leaks_into_a_sent_email(monkeypatch):
    msg, body = _build(monkeypatch, "http://localhost:8000")
    assert "localhost" not in body
    assert "localhost" not in (msg["List-Unsubscribe"] or "")


def test_localhost_falls_back_to_mailto(monkeypatch):
    msg, body = _build(monkeypatch, "http://localhost:8000")
    assert _footer_href(body).startswith("mailto:")
    assert "<mailto:" in msg["List-Unsubscribe"]
    assert "<https" not in msg["List-Unsubscribe"]


def test_read_online_link_hidden_when_unreachable(monkeypatch):
    _, body = _build(monkeypatch, "http://localhost:8000")
    assert "Read full briefing online" not in body


# ── The deployed case ────────────────────────────────────────────────────────

def test_https_host_offers_both_routes_and_one_click(monkeypatch):
    msg, body = _build(monkeypatch, "https://briefing.fly.dev")
    header = msg["List-Unsubscribe"]
    assert "<https://briefing.fly.dev/api/newsletter/unsubscribe" in header
    assert "<mailto:" in header
    assert msg["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert _footer_href(body).startswith("https://briefing.fly.dev")
    assert "Read full briefing online" in body


def test_plain_http_host_does_not_claim_one_click(monkeypatch):
    msg, _ = _build(monkeypatch, "http://192.168.1.50:8000")
    assert msg["List-Unsubscribe-Post"] is None
    assert "<mailto:" in msg["List-Unsubscribe"]


# ── Header is always present, always per-recipient ───────────────────────────

def test_unsubscribe_header_is_per_recipient(monkeypatch):
    import services.newsletter.emailer as emailer
    import services.newsletter.renderer as renderer
    import services.newsletter.unsubscribe as unsub
    import core.config
    patched = _settings("https://briefing.fly.dev")
    for module in (core.config, emailer, renderer, unsub):
        monkeypatch.setattr(module, "settings", patched)

    html = renderer.render_email_html(_newsletter())
    a = emailer._build_message(html, "B", "alice@example.com")["List-Unsubscribe"]
    b = emailer._build_message(html, "B", "bob@example.com")["List-Unsubscribe"]
    assert a != b, "each recipient must get their own signed token"
    assert "alice" in a and "bob" in b


def test_mailto_carries_a_verifiable_token(monkeypatch):
    import core.config
    import services.newsletter.unsubscribe as unsub
    patched = _settings("http://localhost:8000")
    monkeypatch.setattr(core.config, "settings", patched)
    monkeypatch.setattr(unsub, "settings", patched)

    link = unsub.make_mailto_link("alice@example.com")
    assert link.startswith("mailto:briefing@example.com")
    assert unsub.make_token("alice@example.com") in link.replace("%0A", "\n")
