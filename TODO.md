# TODO

## Hosting

> **Fly.io is no longer free.** Free allowances were removed for new accounts in
> October 2024; new signups get a trial of 2 VM hours or 7 days, then metered
> billing. The smallest always-on machine is ~$2/month, realistically $5–25
> once egress and an IP are counted. The plan below still works, it just costs
> money now — see the alternatives before committing.
>
> **Genuinely free options for this stack:**
> - **Oracle Cloud Always Free** — up to 4 ARM cores and 24 GB RAM, no expiry.
>   Enough to host the API, Postgres *and* Ollama, which would take the whole
>   pipeline off the laptop. Most setup effort, biggest payoff.
> - **Render** — permanent free web tier, sleeps after 15 min idle. A ~50s cold
>   start is fine for an unsubscribe link. Check the Postgres terms separately.
> - **Koyeb** — no sleep, no card required. **Railway** — $5/month credit.
>
> Until something is deployed, unsubscribe works through the `mailto:` route in
> the `List-Unsubscribe` header, which needs no hosting at all.

- [ ] **Migrate Postgres to Supabase** — create a free project at [supabase.com](https://supabase.com), enable the `vector` extension, then point `DATABASE_URL` at the Supabase connection string. Run `alembic upgrade head` once from the local machine to initialise the schema. Update `.env` on both the local pipeline machine and the deployed server.
  - Note: migration `008` creates an HNSW index on `articles.embedding`. Confirm the Supabase plan's pgvector version is ≥ 0.5.0 before migrating.

- [ ] **Deploy the API server** — the Dockerfile is production-ready. Whichever host you pick, remove the `--reload` flag and the source bind-mount from `docker-compose.yml` first.

  **Environment to set on the host:**
  ```
  DATABASE_URL       — Supabase (or other managed Postgres) connection string
  APP_ENV            — production
  PUBLIC_URL         — https://<your-host>   (must be reachable; production
                       refuses to start on a loopback value)
  SMTP_USER          — sending address
  SMTP_PASSWORD      — Gmail app password
  RECIPIENT_EMAILS   — comma-separated recipients
  UNSUBSCRIBE_SECRET — long random string, NOT the SMTP password
  API_KEY            — long random string; required before exposing the server
  ```

- [ ] **Update local `.env` for split deployment** — point `DATABASE_URL` at the managed Postgres and `PUBLIC_URL` at the deployed server, so unsubscribe and "read online" links in emails resolve for recipients rather than only on this machine.

- [ ] **Strip development-only settings from the production start command** — remove `--reload` and the `.:/app` bind-mount from `docker-compose.yml`. `--reload` runs a file-watching supervisor that costs memory and restarts on any write.

## Sources

- [x] **Add source metadata for ranking and balancing** — metadata is on every
  article AND is now consumed: `adapter._score` scales each story by the mean
  `weight` of its sources via `services/ingestion/source_catalog.py`, so news
  outranks analysis outranks research at equal engagement.

  ### Goal
  Upgrade `sources.yaml` so the pipeline can distinguish between general news, analysis, and research sources, and control their influence during story ranking.

  ### Required Changes

  1. Update every source entry in `sources.yaml` to include:
     - `source_type` — one of `"news"`, `"analysis"`, `"research"`
     - `weight` — numeric ranking influence score

  2. Keep existing fields unchanged: `name`, `url`, `domain`, `tier`

  3. Weight rules (initial defaults):
     | source_type | tier 1 | tier 2 |
     |-------------|--------|--------|
     | news        | 1.0    | 0.9    |
     | analysis    | 0.7    | 0.6    |
     | research    | 0.4    | 0.3    |

  4. Update ingestion pipeline so each created `Article` includes:
     - `source_type`
     - `source_weight`

  ### Expected Outcome
  - Each article carries source metadata.
  - Future story ranking can use source weights.
  - Newsletter remains general-purpose while research sources act as supporting context.

## Database Maintenance

- [x] **Delete unclustered articles** — `archive.py` now filters on `created_at`
  rather than cluster membership, which is what finally reaches rows with
  `cluster_id IS NULL`. There were 261 of them.

- [x] **VACUUM ANALYZE after archive** — runs against `articles`,
  `story_clusters`, `knowledge_stories` and `newsletters` under AUTOCOMMIT.
  Skip with `--no-vacuum`.

- [x] **Strip old newsletter HTML** — `html_content` is emptied past the article
  window; the metadata row is kept and never deleted. Re-render with
  `send_newsletter.py --rerender`.

- [x] **Schedule archive in cron installer** — added to both the cron and
  systemd paths, weekly on Sunday at 03:00. It runs `--dry-run` and only
  reports to `logs/archive.log`; deletion stays a manual step.

- [x] **Tiered retention** — `ARCHIVE_KEEP_ARTICLES_DAYS` (90) and
  `ARCHIVE_KEEP_STORIES_DAYS` (365). `ARCHIVE_KEEP_DAYS` still works and seeds
  the article window.

- [ ] **Make crash recovery actually resume** — `pipeline_runs` rows are written
  by every durable step but only step 5 reads them. The expensive step (9,
  knowledge generation) relies instead on skipping clusters that already have a
  saved story — and that check cannot work across a restart, because
  `clusterer.py` mints fresh `uuid4` cluster IDs on every run, so re-clustered
  articles never match the stored ones. Fix by deriving cluster IDs from
  content, or by reloading the run's clusters from the database when
  `save_clusters` is already recorded for today.

- [ ] **Drop the duplicate URL index** — `articles` carries both
  `articles_url_key` and `ix_articles_url`, two unique btree indexes on the
  same column. One is redundant write cost on every insert.
