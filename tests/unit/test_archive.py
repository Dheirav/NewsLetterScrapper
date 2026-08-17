"""
Tests for scripts/archive.py retention behaviour.

Regressions guarded here:

1. The old script deleted only articles belonging to an expired cluster
   (``cluster_id IN (...)``). Articles that never clustered — paywall failures
   and singletons below min_cluster_articles — accumulated forever.

2. Retention was a single window, so ageing out bulky article rows also
   destroyed the knowledge stories built from them. With the pipeline idle for
   four months, a default run would have deleted every story in the database.

3. Deleted rows were never VACUUMed, so no disk was actually returned.

The delete path is exercised with a recording stub rather than a live database:
what matters is which predicates are issued, and running the real statements
against real data is exactly what these tests exist to prevent.
"""
from datetime import date, timedelta

import pytest

from core.config import Settings


# ── Configuration ────────────────────────────────────────────────────────────

def test_tiered_windows_have_sensible_defaults():
    s = Settings()
    assert s.archive_keep_articles_days == 90
    assert s.archive_keep_stories_days == 365
    assert s.archive_keep_stories_days > s.archive_keep_articles_days, (
        "stories are the pipeline's output and must outlive their raw material"
    )


def test_legacy_key_still_drives_article_retention():
    """Existing .env files set only ARCHIVE_KEEP_DAYS."""
    s = Settings(archive_keep_days=45)
    assert s.archive_keep_articles_days == 45


def test_explicit_article_window_wins_over_legacy_key():
    s = Settings(archive_keep_days=45, archive_keep_articles_days=120)
    assert s.archive_keep_articles_days == 120


# ── Runaway-delete guard ─────────────────────────────────────────────────────

class _StubSession:
    def __init__(self):
        self.statements = []

    async def execute(self, stmt, *a, **k):
        self.statements.append(stmt)
        class R:
            rowcount = 0
            def scalar(self): return 0
        return R()

    async def flush(self):
        pass


@pytest.fixture
def stub_archive(monkeypatch):
    """
    Run archive() against a stub session with synthetic survey counts.

    The factory is passed EXPLICITLY on every call rather than relying on
    monkeypatching `arch.get_session`. Patching the module attribute is not
    sufficient on its own and the failure is silent: if archive() ever again
    binds get_session as a default argument, that default is captured at import
    and the patch is ignored — which is how a unit test managed to delete from
    the live database.
    """
    import scripts.archive as arch
    from contextlib import asynccontextmanager

    session = _StubSession()

    @asynccontextmanager
    async def fake_session():
        yield session

    # Belt: patch the module reference too.
    monkeypatch.setattr(arch, "get_session", fake_session)

    # Braces: any real connection attempt from this test must blow up loudly
    # rather than quietly succeed against DATABASE_URL.
    import core.db.session as db_session

    def _forbidden(*_a, **_kw):
        raise AssertionError(
            "a unit test tried to open a real database connection; "
            "pass session_factory explicitly"
        )

    monkeypatch.setattr(db_session, "get_session", _forbidden)

    async def no_vacuum():
        session.statements.append("VACUUM")

    monkeypatch.setattr(arch, "_vacuum", no_vacuum)

    def run(**kwargs):
        kwargs.setdefault("session_factory", fake_session)
        return arch.archive(**kwargs)

    return run, session


def _survey_returning(counts):
    async def _fake(session, article_cutoff, story_cutoff):
        return counts
    return _fake


BASE_COUNTS = {
    "articles_total": 100, "articles_expired": 10, "articles_unclustered": 3,
    "stories_expired": 1, "clusters_expired": 1, "newsletters_to_strip": 1,
    "runs_expired": 1, "failed_expired": 0,
}


async def test_guard_blocks_deleting_most_of_the_table(stub_archive, monkeypatch):
    """
    The real scenario: the pipeline stops running, every row ages out, and a
    routine archive would empty the database.
    """
    run_archive, session = stub_archive
    counts = {**BASE_COUNTS, "articles_total": 14097, "articles_expired": 14097}
    import scripts.archive as arch
    monkeypatch.setattr(arch, "_survey", _survey_returning(counts))

    with pytest.raises(SystemExit) as exc:
        await run_archive(article_days=90, story_days=365)

    assert exc.value.code == 1
    assert session.statements == [], "nothing may be executed once the guard trips"


async def test_force_overrides_the_guard(stub_archive, monkeypatch):
    run_archive, session = stub_archive
    counts = {**BASE_COUNTS, "articles_total": 14097, "articles_expired": 14097}
    import scripts.archive as arch
    monkeypatch.setattr(arch, "_survey", _survey_returning(counts))

    await run_archive(article_days=90, story_days=365, force=True, vacuum=False)
    assert session.statements, "with --force the deletes should be issued"


async def test_normal_proportions_pass_without_force(stub_archive, monkeypatch):
    run_archive, session = stub_archive
    import scripts.archive as arch
    monkeypatch.setattr(arch, "_survey", _survey_returning(BASE_COUNTS))

    await run_archive(article_days=90, story_days=365, vacuum=False)
    assert session.statements


async def test_dry_run_executes_nothing(stub_archive, monkeypatch):
    run_archive, session = stub_archive
    import scripts.archive as arch
    monkeypatch.setattr(arch, "_survey", _survey_returning(BASE_COUNTS))

    await run_archive(article_days=90, story_days=365, dry_run=True)
    assert session.statements == []


async def test_dry_run_is_never_blocked_by_the_guard(stub_archive, monkeypatch):
    """Reporting must stay available precisely when the numbers look alarming."""
    run_archive, session = stub_archive
    counts = {**BASE_COUNTS, "articles_total": 100, "articles_expired": 100}
    import scripts.archive as arch
    monkeypatch.setattr(arch, "_survey", _survey_returning(counts))

    await run_archive(article_days=90, story_days=365, dry_run=True)
    assert session.statements == []


async def test_vacuum_runs_by_default_and_can_be_skipped(stub_archive, monkeypatch):
    run_archive, session = stub_archive
    import scripts.archive as arch
    monkeypatch.setattr(arch, "_survey", _survey_returning(BASE_COUNTS))

    await run_archive(article_days=90, story_days=365)
    assert "VACUUM" in session.statements

    session.statements.clear()
    await run_archive(article_days=90, story_days=365, vacuum=False)
    assert "VACUUM" not in session.statements


# ── Statement shape ──────────────────────────────────────────────────────────

async def test_articles_are_deleted_by_age_not_cluster_membership(stub_archive, monkeypatch):
    """
    The fix for orphaned rows: filtering on created_at reaches articles whose
    cluster_id is NULL, which a `cluster_id IN (...)` predicate never could.
    """
    run_archive, session = stub_archive
    import scripts.archive as arch
    monkeypatch.setattr(arch, "_survey", _survey_returning(BASE_COUNTS))

    await run_archive(article_days=90, story_days=365, vacuum=False)

    article_deletes = [
        str(s) for s in session.statements
        if "DELETE FROM articles" in str(s)
    ]
    assert len(article_deletes) == 1
    sql = article_deletes[0]
    assert "created_at" in sql
    assert "cluster_id IN" not in sql, (
        "filtering by cluster membership is what stranded the orphans"
    )


async def test_newsletters_are_stripped_not_deleted(stub_archive, monkeypatch):
    run_archive, session = stub_archive
    import scripts.archive as arch
    monkeypatch.setattr(arch, "_survey", _survey_returning(BASE_COUNTS))

    await run_archive(article_days=90, story_days=365, vacuum=False)

    rendered = [str(s) for s in session.statements]
    assert any("UPDATE newsletters" in s for s in rendered)
    assert not any("DELETE FROM newsletters" in s for s in rendered), (
        "newsletter metadata is small and worth keeping; only the HTML goes"
    )


async def test_stories_outlive_their_articles(stub_archive, monkeypatch):
    """
    With the default windows a story is kept for a year while its articles go
    at 90 days, so the two deletes must use different cutoffs.
    """
    run_archive, session = stub_archive
    import scripts.archive as arch
    monkeypatch.setattr(arch, "_survey", _survey_returning(BASE_COUNTS))

    await run_archive(article_days=90, story_days=365, vacuum=False)

    compiled = [
        s.compile(compile_kwargs={"literal_binds": True})
        for s in session.statements if hasattr(s, "compile")
    ]
    article_sql = [str(c) for c in compiled if "DELETE FROM articles" in str(c)][0]
    story_sql = [str(c) for c in compiled if "DELETE FROM knowledge_stories" in str(c)][0]

    today = date.today()
    assert str(today - timedelta(days=90)) in article_sql
    assert str(today - timedelta(days=365)) in story_sql
