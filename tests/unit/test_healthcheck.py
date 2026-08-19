"""
Tests for the pipeline health check.

This exists because every real failure in this project has been silent. A model
named in OLLAMA_LLM_MODEL but not installed makes every LLM call raise, but
labeler.py catches per cluster and generator.py dead-letters each failure — so
the run finishes in twenty minutes with exit code 0, writes no stories, sends no
email, and reports nothing. Unattended, that is indistinguishable from success.

The check has to name the cause, not just the symptom, or it is just a second
way of noticing no email arrived.
"""
from datetime import date

import pytest

from scripts.healthcheck import FAIL, OK, WARN, Check, Report, check_model_installed


def _report():
    return Report(target=date(2026, 8, 20))


# ── Severity rollup ──────────────────────────────────────────────────────────

def test_all_ok_is_healthy():
    r = _report()
    r.add("a", OK, "fine")
    r.add("b", OK, "fine")
    assert r.healthy is True
    assert r.worst == OK


def test_any_fail_dominates():
    r = _report()
    r.add("a", OK, "fine")
    r.add("b", WARN, "hmm")
    r.add("c", FAIL, "broken")
    assert r.worst == FAIL
    assert r.healthy is False


def test_warn_is_not_healthy_but_is_not_fail():
    """A dead-letter row is worth surfacing without implying the run failed."""
    r = _report()
    r.add("a", OK, "fine")
    r.add("b", WARN, "2 clusters failed generation")
    assert r.worst == WARN
    assert r.healthy is False


# ── The message has to be actionable ─────────────────────────────────────────

def test_failing_checks_carry_a_fix():
    r = _report()
    r.add("ollama model", FAIL, "llama3 not installed", "ollama pull llama3")
    text = r.as_text()
    assert "fix: ollama pull llama3" in text


def test_fix_is_hidden_for_passing_checks():
    """Advice next to a green line is noise."""
    r = _report()
    r.add("ollama model", OK, "installed", "ollama pull llama3")
    assert "fix:" not in r.as_text()


def test_text_names_the_date_and_overall_state():
    r = _report()
    r.add("a", FAIL, "broken")
    text = r.as_text()
    assert "2026-08-20" in text
    assert "FAIL" in text


def test_log_pointer_only_when_something_is_wrong():
    healthy = _report(); healthy.add("a", OK, "fine")
    broken = _report(); broken.add("a", FAIL, "broken")
    assert "pipeline.log" not in healthy.as_text()
    assert "pipeline.log" in broken.as_text()


# ── Model detection: the specific silent failure ─────────────────────────────

class _FakeOllama:
    def __init__(self, names):
        self._names = names

    def list(self):
        return {"models": [{"model": n} for n in self._names]}


@pytest.fixture
def fake_ollama(monkeypatch):
    def _install(names):
        import sys
        monkeypatch.setitem(sys.modules, "ollama", _FakeOllama(names))
    return _install


def test_missing_model_is_a_failure_with_the_pull_command(fake_ollama, monkeypatch):
    from core.config import settings
    monkeypatch.setattr(settings, "ollama_llm_model", "llama3.2")
    fake_ollama(["llama3:latest", "nomic-embed-text:latest"])

    r = _report()
    check_model_installed(r)

    c = next(c for c in r.checks if c.name == "ollama model")
    assert c.status == FAIL
    assert "llama3.2" in c.detail
    assert c.fix == "ollama pull llama3.2"


def test_bare_name_matches_the_latest_tag(fake_ollama, monkeypatch):
    """OLLAMA_LLM_MODEL=llama3 must match an installed llama3:latest."""
    from core.config import settings
    monkeypatch.setattr(settings, "ollama_llm_model", "llama3")
    fake_ollama(["llama3:latest"])

    r = _report()
    check_model_installed(r)
    assert next(c for c in r.checks if c.name == "ollama model").status == OK


def test_a_similarly_named_model_does_not_count(fake_ollama, monkeypatch):
    """llama3.2 installed must not satisfy a request for llama3."""
    from core.config import settings
    monkeypatch.setattr(settings, "ollama_llm_model", "llama3")
    fake_ollama(["llama3.2:latest"])

    r = _report()
    check_model_installed(r)
    assert next(c for c in r.checks if c.name == "ollama model").status == FAIL


def test_unreachable_ollama_is_reported_not_swallowed(monkeypatch):
    import sys

    class _Broken:
        def list(self):
            raise ConnectionError("connection refused")

    monkeypatch.setitem(sys.modules, "ollama", _Broken())
    r = _report()
    check_model_installed(r)

    c = next(c for c in r.checks if c.name == "ollama reachable")
    assert c.status == FAIL


# ── Alerts must not go through the newsletter path ───────────────────────────

def test_alert_recipient_falls_back_without_reaching_subscribers():
    """
    An operational alert is for the operator. It must never be addressed to the
    recipient list, and must not be filtered by the unsubscribe table.
    """
    from core.config import Settings

    # Every field is passed explicitly: Settings() also reads .env, so omitting
    # one lets the developer's real value leak into the assertion.
    base = dict(smtp_user="smtp@example.com", recipient_emails="a@x.com,b@x.com")

    s = Settings(alert_email="ops@example.com", test_email="t@example.com", **base)
    assert s.alert_recipient == "ops@example.com"

    s = Settings(alert_email="", test_email="t@example.com", **base)
    assert s.alert_recipient == "t@example.com"

    s = Settings(alert_email="", test_email="", **base)
    assert s.alert_recipient == "smtp@example.com", (
        "with nothing else set, alerts must still reach the SMTP account owner"
    )

    # The recipient list is never a fallback — an alert is not a briefing.
    assert "a@x.com" not in s.alert_recipient


def test_healthcheck_does_not_import_the_newsletter_emailer():
    """
    send_email() filters opted-out addresses and injects unsubscribe links —
    correct for a briefing, wrong for an alert that must always arrive.
    """
    import pathlib
    src = pathlib.Path("scripts/healthcheck.py").read_text()
    assert "from services.newsletter.emailer import" not in src
    assert "send_email(" not in src
