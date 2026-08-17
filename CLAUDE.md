# Intelligence Briefing — working notes

Self-hosted daily news briefing. A nightly pipeline ingests 38 RSS feeds,
clusters articles into stories, writes deep-knowledge analysis with a local LLM,
and emails an HTML briefing. Everything runs locally: Ollama for inference,
Postgres + pgvector for storage. No external AI APIs.

## Commands

```bash
python scripts/run_pipeline.py              # the nightly run (15–30 min)
python scripts/run_pipeline.py --test       # sends only to TEST_EMAIL, does not mark sent
python scripts/run_pipeline.py --mode detailed   # 5 LLM calls/cluster instead of 1

uvicorn apps.api.main:app --reload --port 8000   # API + web reader + graph explorer

venv/bin/python -m pytest -q                # unit + integration
venv/bin/python -m pytest tests/unit -q     # unit only, needs no database
node tests/js/tracker.test.mjs              # reading-tracker tests (separate runner)

python scripts/archive.py --dry-run         # ALWAYS dry-run first; it deletes
python scripts/explore_db.py --stats        # read-only DB browser
alembic upgrade head                        # apply migrations
```

Use `venv/bin/python`, not system `python3` — dependencies live in `venv/` only.

## Architecture

`services/*` are the pipeline stages, each a `<work>.py` plus a `repository.py`.
Plain dataclasses from `core/schemas/models.py` pass between stages; conversion
to ORM happens only at the repository boundary. `core/config.py` is the single
settings source — **never read `os.environ` directly**.

```
ingestion    feed_reader → scraper → deduplicator → repository
understanding  embedder → clusterer (HDBSCAN) → labeler → repository
             + ingestion/semantic_dedup (runs after embedding, needs vectors)
knowledge    generator (concise|detailed) → reliability → repository
personalization  profiler → adapter
newsletter   assembler → renderer (web + email) → emailer
```

## Conventions that will bite you

**Never touch an `AsyncSession` from concurrent coroutines.** Every fan-out
gathers results first, then writes sequentially — see `embedder.py` and
`generator.py`. This is deliberate and commented in several places.

**Blocking work goes through `run_in_executor`.** Ollama, feedparser,
trafilatura and HDBSCAN are all synchronous.

**The pipeline reads the clock exactly once,** at the top of `run()`, and
threads `run_date` through every write. `save_clusters`, `save_knowledge_story`
and `generate_knowledge_stories` all *require* it — no defaults, deliberately.
A run starting at 23:50 finishes after midnight, and re-reading `date.today()`
downstream used to split one run's output across two dates.

**Failures degrade, they do not raise.** Bad feed → skipped. Failed scrape →
keeps the RSS summary and flags `content_quality="low"`. Failed generation →
row in `failed_generations`.

**Retries wrap every Ollama call** via `tenacity`: 3 attempts, exponential
backoff.

**A missing Ollama model fails silently.** `labeler.py` catches per cluster and
`generator.py` writes each failure to `failed_generations`, so a model named in
`OLLAMA_LLM_MODEL` but not installed lets the pipeline run for 20 minutes,
produce zero stories, log "No knowledge stories generated", and send nothing.
This is what stopped the pipeline for four months. Check `ollama list` first
whenever briefings stop arriving.

## Things that are easy to get wrong

**Dedup similarity thresholds are not free parameters.** Two outlets covering
the same event score ~0.96 and *must* survive — that is the signal clustering
looks for and what `knowledge/reliability.py` grades as cross-confirmation.
Only republication of the same article (0.99+) should collapse. Do not lower
`DEDUP_SEMANTIC_THRESHOLD` below ~0.97 without re-measuring.

**`_domains.py` keywords match on word boundaries.** They used to be raw
substrings, so `"rate"` fired inside "corporate" and `"app"` inside "happened".
Add whole words or whole phrases; do not pad with spaces.

**`API_KEY` protection is default-deny.** Anything not in `_PUBLIC_EXACT` /
`_PUBLIC_PATTERNS` in `apps/api/main.py` needs the key, including new routes.
Public means "a recipient's browser must reach it without a credential" —
newsletter reading, unsubscribe, the reading tracker. Browsers authenticate at
`/unlock` (cookie) because the dashboard is plain `<a href>` links; scripts use
`X-API-Key`.

**`PUBLIC_URL` ends up inside sent emails** — the unsubscribe link and the "read
online" link. A loopback value ships dead links to every recipient, so
production refuses to start on one.

**The reading tracker lives inside `templates/newsletter_web.html`.** It is
tested by a Node harness that extracts the `<script>` block from the template,
so edits there are covered — but only if you run the JS suite, which pytest does
not.

**`archive.py` deletes.** Retention is tiered: articles 90 days, stories 365.
It refuses to run when most of the article table would go, because that is what
happens when the pipeline has been idle.

**Anything that opens its own session can escape a test fixture.** Integration
tests isolate into a throwaway schema, but that only covers sessions the test
creates — code calling `get_session()` itself gets a fresh connection from the
global engine, pointed at whatever `DATABASE_URL` names. `archive()` therefore
takes an injectable `session_factory`, resolved at CALL time, never as a default
argument: a default binds the original function at import, so patching the
module attribute is silently ignored. Both mistakes have destroyed live data
here. `tests/integration/conftest.py` redirects every `get_session` reference as
a backstop, and `tests/unit/test_archive.py` makes a real connection raise.

**`sources.yaml` is parsed in exactly one place** —
`services/ingestion/source_catalog.py`. Both the reliability grading (tiers) and
the personalisation ranking (weights) read it through there, so the two cannot
drift. `adapter._score` multiplies each story by the mean weight of its sources,
which is how news outranks analysis outranks research at equal engagement.

**`APP_ENV` is not a logging switch.** Use `SQL_ECHO=false` for quiet CLI
output. Two scripts used to set `APP_ENV=production` to silence SQL echo, which
put read-only tools under production validation and broke them the moment a
production-only check was added.

## Known gaps

- **Crash recovery does not really resume.** `pipeline_runs` rows are written by
  every durable step but only step 5 reads them. Step 9 relies on skipping
  clusters that already have a story, and that cannot work across a restart:
  `clusterer.py` mints fresh `uuid4` cluster IDs each run, so re-clustered
  articles never match stored ones.
- **Personalisation only reorders within a section.** `adapt_newsletter` ranks
  by engagement, then `assemble` re-sorts by domain. This is intended — the
  README says so — but it does mean engagement never moves a story across
  section boundaries.
- **Duplicate URL index.** `articles` carries both `articles_url_key` and
  `ix_articles_url` — two unique btrees on the same column.
