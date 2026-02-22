# Intelligence Briefing

A self-hosted, AI-powered daily news briefing system. It ingests articles from 25 curated RSS feeds, semantically clusters them into stories, generates structured deep-knowledge analysis via a local LLM, personalises the ordering to your reading habits, and delivers a daily HTML newsletter to your inbox.

Everything runs locally — no external AI APIs, no cloud dependency.

---

## What it does

Each day the pipeline runs through 15 steps automatically:

```
RSS feeds → scrape full text → deduplicate → persist
    → embed (nomic-embed-text) → cluster (HDBSCAN) → label (LLM)
    → knowledge generation (llama3.2) → reliability analysis
    → personalise order → render HTML → email
```

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
│   ├── init_db.py          Create DB tables
│   ├── explore_db.py       Read-only DB browser (CLI)
│   └── crud_db.py          Full CRUD CLI tool
├── migrations/             Alembic migration versions
└── templates/
    ├── newsletter.html     Jinja2 newsletter template
    └── graph.html          Obsidian-style knowledge graph UI
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

## Sharing the app externally (port forwarding)

To expose your local server to anyone outside your network, use one of the following tunnelling options. Make sure the API server is already running before starting a tunnel.

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

The system tracks your reading behaviour through the newsletter's client-side JavaScript (time spent, scroll depth, sections opened). After enough history it weights stories by topic engagement, source affinity, and reading depth preference. Stories you consistently read fully appear higher; those you skip get trimmed to shorter summaries.

---

## News sources

25 curated sources across 7 domains, configured in `services/ingestion/sources.yaml`:

| Domain | Sources |
|---|---|
| World | Reuters, AP, BBC, Al Jazeera, The Guardian, Foreign Affairs |
| Technology | Ars Technica, The Verge, MIT Tech Review, Wired, Hacker News |
| Science | Nature News, New Scientist, Science Daily |
| Economy | Financial Times, The Economist, Bloomberg Markets |
| AI | DeepMind Blog, The Batch (deeplearning.ai) |
| Policy | Politico, Brookings |
| Health | STAT News, WHO News |

Add or remove sources by editing `sources.yaml` — no code changes needed.
