# Setup Guide

Step-by-step instructions to get the Intelligence Briefing system running from scratch.

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | ≥ 3.11 | `python3 --version` |
| PostgreSQL | ≥ 14 | with pgvector extension |
| Ollama | latest | [ollama.com](https://ollama.com) |
| Git | any | to clone the repo |

---

## 1. Clone and create a virtual environment

```bash
git clone <your-repo-url> NewsLetterScrapper
cd NewsLetterScrapper

python3 -m venv .venv
source .venv/bin/activate        # on Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

---

## 2. PostgreSQL + pgvector

### Install PostgreSQL (Ubuntu/Debian)

```bash
sudo apt update
sudo apt install postgresql postgresql-contrib
sudo systemctl start postgresql
sudo systemctl enable postgresql
```

### Install pgvector

```bash
sudo apt install postgresql-server-dev-all build-essential git

git clone https://github.com/pgvector/pgvector.git
cd pgvector
make
sudo make install
cd ..
```

### Create the database and user

```bash
sudo -u postgres psql << 'EOF'
CREATE USER intelligence WITH PASSWORD 'your_password_here';
CREATE DATABASE intelligence_db OWNER intelligence;
\c intelligence_db
CREATE EXTENSION vector;
EOF
```

> **WSL users:** if `sudo -u postgres psql` hangs, start the service first:
> ```bash
> sudo service postgresql start
> ```

---

## 3. Ollama + models

### Install Ollama

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

### Pull required models

```bash
ollama pull llama3.2          # ~2 GB — story generation
ollama pull nomic-embed-text  # ~274 MB — semantic embeddings
```

### Verify Ollama is running

```bash
ollama list
# Should show both models
```

Ollama starts automatically as a systemd service after installation. If it's not running:

```bash
ollama serve &
```

---

## 4. Environment configuration

Copy the example and fill in your values:

```bash
cp .env.example .env
```

Edit `.env`:

```env
# ── Database ──────────────────────────────────────────────────────────────────
DATABASE_URL=postgresql+asyncpg://intelligence:your_password_here@localhost:5432/intelligence_db

# ── Ollama ─────────────────────────────────────────────────────────────────────
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_LLM_MODEL=llama3.2
OLLAMA_EMBED_MODEL=nomic-embed-text

# ── Email (Gmail example) ──────────────────────────────────────────────────────
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=you@gmail.com
SMTP_PASSWORD=your_app_password   # Gmail: Settings → Security → App Passwords
RECIPIENT_EMAIL=you@gmail.com
EMAIL_FROM_NAME=Intelligence Briefing

# ── App ────────────────────────────────────────────────────────────────────────
APP_ENV=production
LOG_LEVEL=INFO
```

> **Gmail note:** You need a [Google App Password](https://myaccount.google.com/apppasswords), not your regular password. Enable 2FA first, then generate an app password for "Mail".

---

## 5. Create the database tables

```bash
python3 scripts/init_db.py
```

You should see confirmation that all tables were created.

Alternatively, run Alembic migrations:

```bash
alembic upgrade head
```

---

## 6. Verify the setup

```bash
# Check DB is reachable and tables exist
python3 scripts/explore_db.py --stats
# Expected: all counts = 0 (empty, not yet run)

# Check Ollama models are available
ollama list

# Check the API starts cleanly
uvicorn apps.api.main:app --host 0.0.0.0 --port 8000
# Visit http://localhost:8000/docs
```

---

## 7. Run the pipeline

```bash
python3 scripts/run_pipeline.py
```

This takes **15–30 minutes** on first run. Watch the logs — each of the 15 steps is printed with a banner.

Once complete:

```bash
# View the newsletter
open http://localhost:8000/api/newsletter/today

# Browse the knowledge graph
open http://localhost:8000/api/graph/

# Check what was generated
python3 scripts/explore_db.py
```

---

## 8. Automate with cron (optional)

Run the pipeline every morning at 6 AM:

```bash
crontab -e
```

Add:

```cron
0 6 * * * cd /path/to/NewsLetterScrapper && /path/to/.venv/bin/python3 scripts/run_pipeline.py >> /var/log/briefing.log 2>&1
```

Find your Python path with `which python3` (inside the venv).

---

## Customising news sources

Edit `services/ingestion/sources.yaml` to add or remove RSS feeds:

```yaml
sources:
  - name: "My Source"
    url: "https://example.com/feed.xml"
    domain: "technology"   # world | technology | science | economy | ai | policy | health
    tier: 2                # 1 = elite, 2 = good, 3 = general
```

No code changes or restarts needed — the file is read each pipeline run.

---

## Troubleshooting

### `pgvector` extension missing

```
ProgrammingError: type "vector" does not exist
```

Connect to the database and run:

```sql
\c intelligence_db
CREATE EXTENSION vector;
```

### Ollama not found / connection refused

```bash
# Check if running
ps aux | grep ollama

# Start manually
ollama serve &

# Or as a service
sudo systemctl start ollama
```

### `total_vram="0 B"` — GPU not detected (WSL2)

This means Ollama is running on CPU, which is fine — just slower. To enable GPU:

1. Check your Windows NVIDIA driver version in PowerShell: `nvidia-smi`
2. You need driver **≥ 527.41**
3. Register WSL CUDA libraries:
   ```bash
   echo "/usr/lib/wsl/lib" | sudo tee /etc/ld.so.conf.d/wsl-cuda.conf
   sudo ldconfig
   sudo systemctl restart ollama
   ```
4. If still not working, update WSL: in Windows PowerShell (as admin):
   ```powershell
   wsl --update
   wsl --shutdown
   ```

### Email not sending

- Confirm `SMTP_USER`, `SMTP_PASSWORD`, and `RECIPIENT_EMAIL` are set in `.env`
- For Gmail, use an **App Password** not your account password
- The pipeline completes successfully even if email fails — the newsletter is still saved to the DB and viewable at `/api/newsletter/today`

### `No new articles today`

This is normal if the pipeline already ran today — deduplication filters out articles with URLs already in the database. Either wait until tomorrow or add new sources to `sources.yaml`.

---

## Environment variables reference

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://postgres:password@localhost:5432/intelligence_db` | Async PostgreSQL connection string |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server address |
| `OLLAMA_LLM_MODEL` | `llama3.2` | Model for story generation |
| `OLLAMA_EMBED_MODEL` | `nomic-embed-text` | Model for embeddings |
| `SMTP_HOST` | `smtp.gmail.com` | SMTP server hostname |
| `SMTP_PORT` | `587` | SMTP port (STARTTLS) |
| `SMTP_USER` | _(empty)_ | SMTP login username |
| `SMTP_PASSWORD` | _(empty)_ | SMTP login password / app password |
| `RECIPIENT_EMAIL` | _(empty)_ | Where to send the newsletter |
| `EMAIL_FROM_NAME` | `Intelligence Briefing` | Display name in email "From" header |
| `APP_ENV` | `development` | Set to `production` to disable SQL echo |
| `LOG_LEVEL` | `INFO` | Python logging level |
| `PROFILE_WINDOW_DAYS` | `30` | Days of reading history used for personalisation |
| `DEDUP_TITLE_THRESHOLD` | `0.85` | Fuzzy title similarity threshold for deduplication |
| `EMBEDDING_DIM` | `768` | Embedding vector size (must match the embed model) |
