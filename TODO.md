# TODO

## Hosting

- [ ] **Migrate Postgres to Supabase** — create a free project at [supabase.com](https://supabase.com), enable the `vector` extension, then point `DATABASE_URL` at the Supabase connection string. Run `alembic upgrade head` once from the local machine to initialise the schema. Update `.env` on both the local pipeline machine and the Fly.io deployment.

- [ ] **Deploy API server to Fly.io** — install the `fly` CLI, run `fly launch` from the project root (Dockerfile is already production-ready, say NO to Fly's managed Postgres). Set secrets via `fly secrets set` (see below). Keep `min_machines_running = 1` in `fly.toml` so the server never sleeps.

  **Secrets to set on Fly:**
  ```
  DATABASE_URL      — Supabase connection string
  APP_ENV           — production
  PUBLIC_URL        — https://<app>.fly.dev
  SMTP_USER         — denewsletter2005@gmail.com
  SMTP_PASSWORD     — Gmail app password
  RECIPIENT_EMAILS  — comma-separated recipients
  UNSUBSCRIBE_SECRET — long random string
  API_KEY           — long random string (locks down the API)
  ```

- [ ] **Update local `.env` for split deployment** — set `DATABASE_URL` to the Supabase URL and `PUBLIC_URL` to the Fly.io URL so unsubscribe links and "Read online" links in emails point to the live server.

- [ ] **Remove `--reload` from production start command** — `fly.toml` / `Dockerfile` CMD should use `uvicorn apps.api.main:app --host 0.0.0.0 --port 8000` (no `--reload`).

## Sources

- [x] **Add source metadata for ranking and balancing**

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

- [ ] **Delete unclustered articles** — `archive.py` only deletes articles belonging to old clusters (`cluster_id IN (...)`). Articles that never clustered (`cluster_id = NULL`) — paywall failures, singletons below `min_cluster_articles` — accumulate indefinitely. Add a second delete pass: `DELETE FROM articles WHERE cluster_id IS NULL AND created_at < cutoff`.

- [ ] **VACUUM ANALYZE after archive** — PostgreSQL marks deleted rows as dead tuples but doesn't reclaim disk until `VACUUM` runs. Add `VACUUM ANALYZE articles, story_clusters, knowledge_stories` at the end of `archive.py` using a raw `asyncpg` connection (requires `AUTOCOMMIT` isolation — cannot run inside a transaction).

- [ ] **Strip old newsletter HTML** — `newsletters` table grows ~150 KB/day (full rendered HTML) and is never touched by the archive. For newsletters older than `ARCHIVE_KEEP_DAYS`, null out `html_content` while keeping the metadata row. The newsletter can be re-rendered on demand via `send_newsletter.py` if ever needed.

- [ ] **Schedule archive in cron installer** — `install_cron.sh` only schedules `run_pipeline.py`. Add a second weekly cron entry (e.g. Sunday 03:00) that runs `scripts/archive.py` automatically. Controlled by `ARCHIVE_KEEP_DAYS` in `.env` (default 90).

- [ ] **Tiered retention** — add two separate config keys: `ARCHIVE_KEEP_ARTICLES_DAYS` (default 90, controls raw text + embeddings) and `ARCHIVE_KEEP_STORIES_DAYS` (default 365, controls knowledge stories + newsletter metadata). This keeps useful long-term history without paying the storage cost of embeddings.
