#!/usr/bin/env bash
# deploy/bootstrap.sh — run ON the fresh Oracle instance, not locally.
#
#   ssh -i ~/.ssh/oracle_briefing ubuntu@<IP>
#   git clone <repo> briefing && cd briefing
#   cp .env.example .env && nano .env      # fill in the values marked REQUIRED
#   ./deploy/bootstrap.sh
#
# Idempotent: safe to re-run after editing .env.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_DIR}"

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

# ── Preconditions ────────────────────────────────────────────────────────────
[[ -f .env ]] || { echo "error: .env missing — copy .env.example and fill it in" >&2; exit 1; }

missing=()
for k in PUBLIC_URL SITE_ADDRESS POSTGRES_PASSWORD API_KEY UNSUBSCRIBE_SECRET SMTP_USER SMTP_PASSWORD; do
  grep -qE "^${k}=.+" .env || missing+=("$k")
done
if (( ${#missing[@]} )); then
  echo "error: these must be set in .env before deploying: ${missing[*]}" >&2
  exit 1
fi

# PUBLIC_URL ends up inside every email. Catching a loopback value here is much
# cheaper than discovering it after a send.
if grep -qE '^PUBLIC_URL=https?://(localhost|127\.0\.0\.1|0\.0\.0\.0)' .env; then
  echo "error: PUBLIC_URL is a loopback address; recipients cannot reach it" >&2
  exit 1
fi

# ── Docker ───────────────────────────────────────────────────────────────────
if ! command -v docker >/dev/null 2>&1; then
  say "Installing Docker"
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER"
  echo "NOTE: log out and back in for group membership, or use sudo docker below."
fi

# ── Firewall ─────────────────────────────────────────────────────────────────
# Oracle's Ubuntu images ship iptables rules that drop inbound traffic even when
# the console Security List allows it. This is the single most common reason a
# correctly configured Oracle instance appears unreachable.
say "Opening ports 80 and 443 in the host firewall"
if command -v iptables >/dev/null 2>&1; then
  for p in 80 443; do
    sudo iptables -C INPUT -p tcp --dport "$p" -j ACCEPT 2>/dev/null \
      || sudo iptables -I INPUT 6 -p tcp --dport "$p" -j ACCEPT
  done
  command -v netfilter-persistent >/dev/null 2>&1 && sudo netfilter-persistent save || \
    sudo sh -c 'iptables-save > /etc/iptables/rules.v4' 2>/dev/null || \
    echo "  warning: could not persist iptables rules; they may not survive reboot"
fi
echo "  Reminder: the Security List for this instance's subnet must also allow"
echo "  ingress on 80 and 443 from 0.0.0.0/0 — that part is console-only."

# ── Bring the stack up ───────────────────────────────────────────────────────
DC="docker compose -f deploy/docker-compose.prod.yml"
say "Building and starting"
$DC up -d --build

say "Waiting for Ollama to finish pulling models (first boot downloads ~5 GB)"
for _ in $(seq 60); do
  if $DC exec -T ollama ollama list 2>/dev/null | grep -qE 'llama|nomic'; then break; fi
  sleep 15
done
$DC exec -T ollama ollama list 2>&1 | sed 's/^/  /' || true

say "Applying migrations"
$DC exec -T app alembic upgrade head 2>&1 | tail -3 | sed 's/^/  /'

say "Health"
$DC ps
$DC exec -T app python -c "
from core.config import settings
print('  APP_ENV        :', settings.app_env)
print('  PUBLIC_URL     :', settings.public_url)
print('  reachable?     :', settings.public_url_is_reachable)
print('  one-click unsub:', settings.supports_one_click_unsubscribe)
print('  API key set    :', bool(settings.api_key))
print('  LLM model      :', settings.ollama_llm_model)
"

cat <<EOF

Next:
  Test run (emails only TEST_EMAIL, does not mark sent):
    $DC exec app python scripts/run_pipeline.py --test

  Schedule it (host cron calling into the container):
    ./scripts/install_cron.sh --docker --time 12:00 --tz Asia/Kolkata

  Logs:
    $DC logs -f app
EOF
