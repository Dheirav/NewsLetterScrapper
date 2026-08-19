# Deploying to Oracle Cloud Always Free

The stack fits the Always Free ARM tier, but only just. As of **15 June 2026**
Oracle halved that tier from 4 OCPU / 24 GB to **2 OCPU / 12 GB**, without
announcing it. Budget accordingly: llama3:8b fits, a 14B model does not.

Memory ceiling set by `docker-compose.prod.yml`:

| service | limit | why |
|---|---|---|
| ollama | 8 GB | llama3:8b needs ~6 GB resident during generation |
| db | 1 GB | ~13 KB per article; 0.7 GB of data at 90-day retention |
| app | 1 GB | FastAPI, idle most of the day |
| caddy | 256 MB | TLS termination |
| **total** | **10.25 GB** | leaves ~1.75 GB for the host OS |

---

## Steps only you can do

These need your identity, your card, and the Oracle console. Nothing here can
be automated from this machine.

### 1. Create the tenancy
<https://cloud.oracle.com/> — a card is required for identity verification.
Always Free resources are not charged. **Pick your home region carefully: it
cannot be changed later.** Choose `ap-singapore-1` — closest to IST, and one of
the two regions where A1 capacity is reliably obtainable.

### 2. Claim an A1 instance
Compute → Instances → Create.

- Image: **Canonical Ubuntu 24.04**
- Shape: **VM.Standard.A1.Flex**, 2 OCPU / 12 GB
- SSH key: paste `~/.ssh/oracle_briefing.pub` (already generated)
- Boot volume: 100 GB (Always Free allows up to 200 GB)

Expect **"Out of host capacity"**. It is the normal experience, not a mistake
on your part. Retry periodically, or try `eu-frankfurt-1` if you have not yet
fixed your home region.

### 3. Open the ports
VCN → Security List → Add Ingress Rules: TCP **80** and **443** from
`0.0.0.0/0`.

This is necessary but *not sufficient* — Oracle's Ubuntu images also ship
iptables rules that drop inbound traffic regardless of the Security List.
`bootstrap.sh` handles the host side. Missing one of the two is the most common
reason a correctly built Oracle instance appears dead.

### 4. Point a hostname at it
Caddy needs a real hostname to obtain a certificate, and HTTPS is what enables
RFC 8058 one-click unsubscribe. A free DuckDNS subdomain is enough.

Without a hostname, set `SITE_ADDRESS=:80` and accept plain HTTP — the app then
correctly refuses to advertise one-click and falls back to `mailto:`.

---

## Steps that are automated

```bash
ssh -i ~/.ssh/oracle_briefing ubuntu@<INSTANCE_IP>

git clone <your-repo-url> briefing && cd briefing
cp .env.example .env
nano .env          # see the required values below
./deploy/bootstrap.sh
```

`bootstrap.sh` installs Docker, opens the host firewall, builds and starts the
stack, waits for the model pull, applies migrations, and prints a health
summary. It refuses to start if any required value is missing, or if
`PUBLIC_URL` is a loopback address — that check exists because a localhost
`PUBLIC_URL` puts dead unsubscribe links in every email actually sent.

### Required in `.env`

| key | value |
|---|---|
| `PUBLIC_URL` | `https://your.host` — goes inside every email |
| `SITE_ADDRESS` | the same hostname, for the certificate |
| `POSTGRES_PASSWORD` | generate one; the dev default is `password` |
| `API_KEY` | protects `/api/graph/*`; required before exposing anything |
| `UNSUBSCRIBE_SECRET` | keep separate from `SMTP_PASSWORD` |
| `SMTP_USER`, `SMTP_PASSWORD`, `RECIPIENT_EMAILS`, `TEST_EMAIL` | delivery |
| `OLLAMA_LLM_MODEL` | `llama3` |

Generate the secrets:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

### Then

```bash
DC="docker compose -f deploy/docker-compose.prod.yml"

$DC exec app python scripts/run_pipeline.py --test     # emails only TEST_EMAIL
./scripts/install_cron.sh --docker --time 12:00 --tz Asia/Kolkata
```

`--docker` makes the schedule drive `docker compose exec` instead of a local
venv, and switches the backup to dumping from inside the `db` container —
`DATABASE_URL` names `db:5432`, which does not resolve from the host, so a
host-side `pg_dump` would fail silently every night.

---

## Migrating the existing database

```bash
# on the laptop
./scripts/backup_db.sh
scp -i ~/.ssh/oracle_briefing ~/briefing-backups/intelligence_db-*.sql.gz ubuntu@<IP>:~/

# on the server
gunzip -c intelligence_db-*.sql.gz | \
  docker compose -f deploy/docker-compose.prod.yml exec -T db psql -U postgres intelligence_db
```

Do this *before* the first scheduled run, and stop the laptop's cron
(`./scripts/install_cron.sh --uninstall`) so two hosts are not briefing the
same recipients.
