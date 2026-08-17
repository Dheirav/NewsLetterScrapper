"""
Tests that one pipeline run stamps all of its output with a single date.

The regression: run_pipeline captured ``today = date.today()`` at startup and
stamped the newsletter with it, but save_knowledge_story and save_clusters each
called ``date.today()`` again at write time. A run starting at 23:50 and
finishing after midnight wrote the newsletter under one date and its stories
and clusters under the next, so ``get_stories_for_date`` returned nothing and
``send_newsletter.py --rerender`` exited 1 for that run.

These use a stub session rather than a database: the property under test is
which date value reaches the ORM object, which needs no persistence.
"""
import inspect
from datetime import date

import pytest

from core.schemas.models import Article, KnowledgeStory, StoryCluster
from services.knowledge.repository import save_knowledge_story
from services.understanding.repository import save_clusters

RUN_DATE = date(2026, 8, 17)      # the date the run started
NEXT_DAY = date(2026, 8, 18)      # what date.today() would return post-midnight


class _Result:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def __iter__(self):
        return iter(self._rows)


class StubSession:
    """Minimal AsyncSession stand-in that records what was added."""

    def __init__(self):
        self.added = []

    async def execute(self, *_args, **_kwargs):
        return _Result()

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass


@pytest.fixture
def frozen_tomorrow(monkeypatch):
    """Make date.today() return the day AFTER the run started."""
    import services.knowledge.repository as kr
    import services.understanding.repository as ur

    class FakeDate(date):
        @classmethod
        def today(cls):
            return NEXT_DAY

    monkeypatch.setattr(kr, "date", FakeDate)
    monkeypatch.setattr(ur, "date", FakeDate)


def _story():
    return KnowledgeStory(
        cluster_id="cluster-1", topic_label="Tariff talks stall",
        executive_summary="S.", context="C.", why_it_matters="M.",
        implications="I.", talking_points=["a"], source_count=2,
        article_urls=["https://example.com/a"], article_sources=["Reuters"],
    )


def _cluster():
    article = Article(
        title="T", source="Reuters", url="https://example.com/a",
        published_at=None, content="body", id=1,
    )
    return StoryCluster(
        cluster_id="cluster-1", topic_label="Tariff talks stall",
        articles=[article], created_at=None,
    )


# ── The midnight case ────────────────────────────────────────────────────────

async def test_story_uses_run_date_not_wall_clock(frozen_tomorrow):
    session = StubSession()
    await save_knowledge_story(_story(), session, RUN_DATE)

    assert len(session.added) == 1
    assert session.added[0].story_date == RUN_DATE, (
        "story must carry the date the run started, not the date it finished"
    )


async def test_cluster_uses_run_date_not_wall_clock(frozen_tomorrow):
    session = StubSession()
    await save_clusters([_cluster()], session, RUN_DATE)

    added = [o for o in session.added if hasattr(o, "cluster_date")]
    assert len(added) == 1
    assert added[0].cluster_date == RUN_DATE


async def test_story_and_cluster_agree_within_a_run(frozen_tomorrow):
    """The newsletter joins stories by date; a split makes it come back empty."""
    session = StubSession()
    await save_knowledge_story(_story(), session, RUN_DATE)
    await save_clusters([_cluster()], session, RUN_DATE)

    dates = {
        getattr(o, "story_date", None) or getattr(o, "cluster_date", None)
        for o in session.added
    }
    assert dates == {RUN_DATE}, f"one run produced multiple dates: {dates}"


# ── The clock must not be reachable as a default ─────────────────────────────

@pytest.mark.parametrize(
    "func", [save_knowledge_story, save_clusters],
    ids=["save_knowledge_story", "save_clusters"],
)
def test_run_date_is_required(func):
    """
    A default of date.today() would silently reintroduce the split for the next
    caller who forgets to pass it, so run_date has no default at all.
    """
    param = inspect.signature(func).parameters["run_date"]
    assert param.default is inspect.Parameter.empty, (
        f"{func.__name__} must not default run_date to the wall clock"
    )


def test_generator_accepts_and_requires_run_date():
    from services.knowledge.generator import generate_knowledge_stories

    param = inspect.signature(generate_knowledge_stories).parameters["run_date"]
    assert param.default is inspect.Parameter.empty


def test_pipeline_reads_the_clock_exactly_once():
    """
    Guards the invariant directly: run() should contain a single date.today()
    call, the one captured at the top and threaded through every step.

    Counted via AST rather than string search so that prose mentioning
    date.today() in a comment or docstring does not register as a call.
    """
    import ast
    import textwrap

    import scripts.run_pipeline as rp

    tree = ast.parse(textwrap.dedent(inspect.getsource(rp.run)))
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "today"
    ]
    assert len(calls) == 1, (
        f"run() should capture the date once and thread it through; found {len(calls)}"
    )
