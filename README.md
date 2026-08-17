# Intelligence Briefing

A self-hosted, AI-powered daily news briefing system. It ingests articles from 38 curated RSS feeds across 10 domains, semantically clusters them into stories, generates structured deep-knowledge analysis via a local LLM, personalises the ordering to your reading habits, and delivers a daily HTML newsletter to your inbox.

Everything runs locally — no external AI APIs, no cloud dependency.

---

## What it does

Each day the pipeline runs through 15 steps automatically:

```
RSS feeds → scrape full text → deduplicate → persist
    → embed (nomic-embed-text) → drop republished stories (pgvector)
    → cluster (HDBSCAN) → label (LLM)
    → knowledge generation (llama3.2) → reliability analysis
    → personalise order → render HTML → email
```

Deduplication happens in three passes, because each catches something the
others cannot: exact URL match against the database, TF-IDF title similarity
within the current batch, then embedding similarity against the last 7 days —
which is what catches a story reappearing under a fresh URL. That last pass is
deliberately conservative (0.985 cosine): two outlets covering the same event
score around 0.96 and **must** survive, since multi-source coverage is exactly
what clustering looks for and what the reliability grading counts.

For each story cluster the LLM produces:
- **Executive summary** — what happened, who's involved
- **Context** — historical background and why it's happening now
- **Why it matters** — real-world impact
- **Implications** — what happens next, second-order effects
- **Talking points** — 5 conversation-ready facts

---

## Architecture

```
NewsLetterScrapper/
├── apps/api/               FastAPI web server
│   └── routers/
│       ├── newsletter.py   Serve rendered HTML newsletter
│       ├── reading.py      Track reading events (time, scroll)
│       ├── reflection.py   Daily AI "today you learned" summary
│       └── graph.py        Knowledge graph explorer + full CRUD API
├── core/
│   ├── config.py           Pydantic settings (loaded from .env)
│   ├── db/                 SQLAlchemy async engine, session, ORM models
│   └── schemas/models.py   In-memory dataclasses passed between services
├── services/
│   ├── ingestion/          RSS fetch, full-text scrape, dedup, persist
│   ├── understanding/      Embed, cluster (HDBSCAN), label clusters
│   ├── knowledge/          LLM story generation, reliability analysis
│   ├── newsletter/         Assemble, render HTML, send email
│   └── personalization/    Reading profile, story reordering
├── scripts/
│   ├── run_pipeline.py     Daily pipeline orchestrator (run this)
│   ├── send_newsletter.py  Resend a newsletter without re-running the pipeline
│   ├── init_db.py          Create DB tables
│   ├── explore_db.py       Read-only DB browser (CLI)
│   └── crud_db.py          Full CRUD CLI tool
├── migrations/             Alembic migration versions
└── templates/
    ├── newsletter_web.html   Jinja2 template — web reader (JS reading tracker, collapsible cards)
    ├── newsletter_email.html Jinja2 template — email version (no JS, fully expanded)
    ├── dashboard.html        Landing page / dashboard UI
    └── graph.html            Obsidian-style knowledge graph UI
```

---

## Tech stack

| Layer | Technology |
|---|---|
| LLM inference | [Ollama](https://ollama.com) — `llama3.2` (stories) + `nomic-embed-text` (embeddings) |
| Database | PostgreSQL with [pgvector](https://github.com/pgvector/pgvector) extension |
| Clustering | HDBSCAN on L2-normalised 768-dim embeddings |
| Web API | FastAPI + uvicorn |
| ORM | SQLAlchemy 2.0 async |
| Scraping | httpx + trafilatura |
| Email | aiosmtplib (STARTTLS/SMTP) |
| Config | pydantic-settings + `.env` |

---

## Running the pipeline

```bash
# Once per day (or via cron)
python3 scripts/run_pipeline.py
```

The pipeline takes 15–30 minutes depending on hardware and how many clusters form.

---

## Web interfaces

Start the API server:

```bash
uvicorn apps.api.main:app --reload --host 0.0.0.0 --port 8000
```

| URL | What it does |
|---|---|
| `http://localhost:8000/api/newsletter/today` | Today's rendered HTML newsletter |
| `http://localhost:8000/api/newsletter/2026-02-21` | Newsletter for a specific date |
| `http://localhost:8000/api/reflection/today` | AI reading reflection + stats |
| `http://localhost:8000/api/graph/` | **Knowledge graph explorer UI** |
| `http://localhost:8000/docs` | Auto-generated API docs (Swagger) |

---

## Security — read before exposing the server

The API is split into a public surface and a private one, controlled by `API_KEY`.

| | Routes |
|---|---|
| **Public** — no credential | `/health`, `/`, `GET /api/newsletter/{date}`, `/api/newsletter/unsubscribe`, `POST /api/events/reading` |
| **Private** — needs the key | all of `/api/graph/*` (including `DELETE`), `/api/reflection/*`, `/api/newsletter/opted-out`, `/docs` |

Anything not on the public list requires the key, including routes added later.

Recipients need the public routes and cannot present a credential, which is why
they stay open. Everything else can delete data, expose every subscriber's email
address, or queue LLM work on your machine.

**Set `API_KEY` before starting any tunnel.** With it empty the check is a no-op
and every route above is reachable by anyone with the URL.

Browsers authenticate once, since the dashboard and graph explorer are ordinary
links and a link cannot carry a header:

```
http://localhost:8000/unlock?key=<your API_KEY>
```

That sets an `HttpOnly` cookie good for 30 days. Scripts use `X-API-Key`
instead:

```bash
curl -H "X-API-Key: $API_KEY" http://localhost:8000/api/graph/stats
```

---

## Sharing the app externally (port forwarding)

To expose your local server to anyone outside your network, use one of the following tunnelling options. Make sure the API server is already running before starting a tunnel.

> Set `API_KEY` and `PUBLIC_URL` first. `PUBLIC_URL` is baked into the
> unsubscribe and "read online" links of every email you send — if it points at
> localhost, those links are dead for everyone but you.

### ngrok (requires free account)

```bash
# Install
snap install ngrok          # or: https://ngrok.com/download

# Authenticate once (get your token at https://dashboard.ngrok.com)
ngrok config add-authtoken <YOUR_TOKEN>

# Forward port 8000
ngrok http 8000
```

ngrok prints a public `https://<id>.ngrok-free.app` URL. Share that URL — it stays active until you stop the process.

### Cloudflare Tunnel (no account needed)

```bash
# Install (one-time)
wget -q https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -O cloudflared \
  && chmod +x cloudflared && sudo mv cloudflared /usr/local/bin

# Forward port 8000 (force TCP to avoid UDP/QUIC blocks on corporate networks)
cloudflared tunnel --url http://localhost:8000 --protocol http2
```

Cloudflare prints a random `https://*.trycloudflare.com` URL. No account or sign-up required.

---

## Knowledge graph explorer

The graph explorer at `/api/graph/` shows an Obsidian-style force-directed graph of your entire knowledge base. Articles, clusters, stories and newsletters are nodes; edges show how they relate.

From the UI you can:
- Browse all nodes and read full story content
- Edit topic labels, summaries, and story fields inline
- Delete bad clusters, articles, or stories
- Filter by time window (1–90 days)
- Search nodes by title or source

---

## CLI database tools

```bash
# Read-only exploration
python3 scripts/explore_db.py                   # overview of last 7 days
python3 scripts/explore_db.py --stats           # row counts per table
python3 scripts/explore_db.py --stories         # list all knowledge stories
python3 scripts/explore_db.py --story 5         # full story detail
python3 scripts/explore_db.py --clusters        # list clusters
python3 scripts/explore_db.py --cluster abc123  # cluster + member articles
python3 scripts/explore_db.py --sources         # article count by source
python3 scripts/explore_db.py --days 14         # change lookback window

# CRUD operations
python3 scripts/crud_db.py articles list
python3 scripts/crud_db.py articles get 42
python3 scripts/crud_db.py stories update 5 --summary "Better summary"
python3 scripts/crud_db.py clusters rename <uuid> "New topic label"
python3 scripts/crud_db.py clusters delete <uuid>    # cascades to story
```

---

## Personalisation

The system tracks your reading behaviour through the newsletter's client-side JavaScript (time spent, scroll depth, sections opened). After enough history it builds a profile of topic engagement, source affinity, and reading depth.

Two things then use it:

**Ordering — within each section.** Stories are ranked by engagement, then
grouped into domain sections, so the ranking decides the order *inside* World,
Technology and so on. Sections themselves always follow the fixed order below.
A story you love in Entertainment will still appear after every World story;
that homogeneous grouping is deliberate.

The rank also scales by source type, using the `weight` field in
`sources.yaml` — news (1.0) outranks analysis (0.7) outranks research (0.4) at
equal engagement, so research sources act as supporting context rather than
leading the briefing. Strong personal engagement still beats a heavier source.

**Depth — per story.** Topics below your engagement threshold get an awareness
treatment: talking points trimmed to three. Stories with three or more
cross-confirming sources are exempt and always shown in full, regardless of
whether the topic interests you.

---

## Email delivery and unsubscribe

Each recipient gets their own message — nobody sees the rest of the list — and
every message carries a `List-Unsubscribe` header, so Gmail and Apple Mail show
their native Unsubscribe button.

Two opt-out routes are offered, and which ones appear depends on `PUBLIC_URL`:

| Route | Available when | Notes |
|---|---|---|
| `mailto:` | always | Needs no hosting, so it works while your machine is off. Carries the same signed HMAC token as the HTTPS route. |
| `https://` | `PUBLIC_URL` is reachable | Advertised as RFC 8058 one-click only when it is also HTTPS, since one-click requires a POST endpoint. |

With `PUBLIC_URL` left at localhost, only the `mailto:` route is offered and the
"Read full briefing online" link is omitted rather than shown broken.

`mailto:` opt-outs arrive as email to `SMTP_USER`; nothing processes them
automatically. Record them with `python scripts/crud_db.py`.

Set `UNSUBSCRIBE_SECRET` to a random string. Without it, tokens are signed with
`SMTP_PASSWORD` — and rotating your mail password would then invalidate every
unsubscribe link already sitting in someone's inbox.

---

## Data retention

Retention is tiered, because the two kinds of data cost very different amounts
to keep:

| | Window | Why |
|---|---|---|
| Articles — raw text + 768-dim embeddings | `ARCHIVE_KEEP_ARTICLES_DAYS` (90) | Effectively all of the disk growth |
| Stories and clusters | `ARCHIVE_KEEP_STORIES_DAYS` (365) | A few KB each; the actual output |
| Newsletter HTML (~150 KB/day) | stripped past the article window | Row kept; re-render with `--rerender` |

```bash
python scripts/archive.py --dry-run     # always start here
python scripts/archive.py               # apply, then VACUUM ANALYZE
```

`archive.py` refuses to run if it would delete most of the article table, and
tells you to widen the window instead. That case is not hypothetical: if the
pipeline has not run for a while, every row falls outside the window and a
routine invocation would empty the database.

---

## News sources

38 curated sources across 10 domains, configured in `services/ingestion/sources.yaml`:

| Domain | Sources |
|---|---|
| World | Reuters, AP, BBC News, Al Jazeera, The Guardian, Foreign Affairs |
| India | The Hindu, Indian Express, Hindustan Times, NDTV, Times of India |
| Policy | Politico, Brookings |
| Economy | Financial Times, The Economist, Bloomberg Markets |
| AI | DeepMind Blog, The Batch (deeplearning.ai) |
| Technology | Ars Technica, The Verge, MIT Tech Review, Wired, Hacker News |
| Science | Nature News, New Scientist, Science Daily |
| Health | STAT News, WHO News |
| Sport | BBC Sport, ESPN, Sky Sports, The Athletic, Yahoo Sports |
| Entertainment | BBC Entertainment, Variety, Hollywood Reporter, Deadline, IGN |

Newsletters are rendered in the order above — all stories from the same domain appear together before the next section begins. Add or remove sources by editing `sources.yaml` — no code changes needed.
