#!/usr/bin/env bash
# scripts/install_cron.sh
# ─────────────────────────────────────────────────────────────────────────────
# Install a daily cron job (or systemd timer) to run the pipeline automatically.
#
# Usage:
#   ./scripts/install_cron.sh             # installs cron job at 06:00 daily
#   ./scripts/install_cron.sh --time 07:30  # custom time (HH:MM, 24h)
#   ./scripts/install_cron.sh --systemd    # install systemd .service + .timer
#                                          # instead of cron (requires sudo)
#   ./scripts/install_cron.sh --uninstall   # remove every entry this installed
#   ./scripts/install_cron.sh --docker      # run inside the compose stack
#   ./scripts/install_cron.sh --time 12:00 --tz Asia/Kolkata   # named timezone
#   ./scripts/install_cron.sh --no-backup  # skip the daily database dump
#   ./scripts/install_cron.sh --no-archive # skip the weekly retention report
#
# Installs three schedules: the pipeline daily, a retention REPORT weekly
# (never a live delete), and a database backup daily.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Prefer the project's virtualenv. `which python3` finds the SYSTEM interpreter,
# which does not have feedparser, hdbscan, sqlalchemy or any other dependency
# installed — so a cron job built from it fails at import time, at 06:00, with
# the traceback going somewhere nobody reads.
if [[ -n "${PYTHON:-}" ]]; then
  :                                              # explicit override wins
elif [[ -x "${REPO_DIR}/venv/bin/python" ]]; then
  PYTHON="${REPO_DIR}/venv/bin/python"
elif [[ -x "${REPO_DIR}/.venv/bin/python" ]]; then
  PYTHON="${REPO_DIR}/.venv/bin/python"
else
  PYTHON="$(command -v python3)"
  echo "warning: no virtualenv found at ${REPO_DIR}/venv — falling back to ${PYTHON}" >&2
  echo "         if the pipeline's dependencies are not installed there, cron runs will fail." >&2
fi

# Logs go inside the repo. /var/log needs root, so a user-installed cron job
# writing there fails silently on every run.
LOG_DIR="${REPO_DIR}/logs"
HOUR="06"
MINUTE="00"
ARCHIVE_DAY="0"        # Sunday
ARCHIVE_HOUR="03"
BACKUP_HOUR=""         # defaults to one hour after the pipeline
SCHED_TZ=""            # interpret --time in this zone, converting to local
USE_DOCKER=false        # run the pipeline inside the compose stack
USE_SYSTEMD=false
WITH_ARCHIVE=true
WITH_BACKUP=true
DO_UNINSTALL=false

# ── Parse arguments ──────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --time)
      IFS=':' read -r HOUR MINUTE <<< "$2"
      shift 2
      ;;
    --systemd)
      USE_SYSTEMD=true
      shift
      ;;
    --no-archive)
      WITH_ARCHIVE=false
      shift
      ;;
    --no-backup)
      WITH_BACKUP=false
      shift
      ;;
    --uninstall)
      DO_UNINSTALL=true
      shift
      ;;
    --tz)
      SCHED_TZ="$2"
      shift 2
      ;;
    --backup-time)
      BACKUP_HOUR="${2%%:*}"
      shift 2
      ;;
    --docker)
      USE_DOCKER=true
      shift
      ;;
    *)
      echo "Unknown argument: $1"
      exit 1
      ;;
  esac
done

# Debian/Ubuntu ship vixie cron 3.0pl1, which does NOT implement CRON_TZ — that
# is a cronie extension. Writing CRON_TZ into the crontab there sets a harmless
# environment variable for the job and changes nothing about when it fires, so
# the schedule silently lands at the wrong hour. Convert to local time instead
# and record both in a comment.
ORIG_TIME="${HOUR}:${MINUTE}"
if [[ -n "${SCHED_TZ}" ]]; then
  if ! command -v python3 >/dev/null 2>&1; then
    echo "error: --tz needs python3 to convert ${SCHED_TZ} into local time" >&2
    exit 1
  fi
  CONVERTED="$(python3 - "$SCHED_TZ" "$HOUR" "$MINUTE" <<'PYEOF'
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
tz, hh, mm = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
try:
    src = ZoneInfo(tz)
except Exception:
    sys.exit(f"unknown timezone: {tz}")
# Use tomorrow so the conversion reflects the offset in effect when it next runs.
day = (datetime.now().date() + timedelta(days=1))
local = datetime(day.year, day.month, day.day, hh, mm, tzinfo=src).astimezone()
print(local.strftime("%H %M"))
PYEOF
)" || { echo "error: timezone conversion failed" >&2; exit 1; }
  HOUR="${CONVERTED%% *}"
  MINUTE="${CONVERTED##* }"
fi

# The backup must land AFTER the pipeline, or it captures the previous day's
# state. Deriving it from the pipeline hour means moving --time cannot silently
# invert the two.
if [[ -z "${BACKUP_HOUR}" ]]; then
  BACKUP_HOUR="$(printf '%02d' $(( (10#${HOUR} + 1) % 24 )))"
  BACKUP_MINUTE="${MINUTE}"
else
  BACKUP_MINUTE="00"
fi

# ── Cron install ─────────────────────────────────────────────────────────────
install_cron() {
  mkdir -p "${LOG_DIR}"

  local marker="# intelligence-briefing"

  # In a containerised deployment the interpreter lives inside the app service,
  # so the host schedule drives `docker compose exec` instead of a local venv.
  local RUN ARCHIVE_RUN
  if [[ "${USE_DOCKER}" == true ]]; then
    local DC="docker compose -f deploy/docker-compose.prod.yml"
    RUN="${DC} exec -T app python scripts/run_pipeline.py"
    ARCHIVE_RUN="${DC} exec -T app python scripts/archive.py --dry-run"
    BACKUP_RUN="./scripts/backup_db.sh --docker"
  else
    RUN="'${PYTHON}' scripts/run_pipeline.py"
    ARCHIVE_RUN="'${PYTHON}' scripts/archive.py --dry-run"
    BACKUP_RUN="./scripts/backup_db.sh"
  fi

  local pipeline_cmd="${MINUTE} ${HOUR} * * * cd '${REPO_DIR}' && ${RUN} >> '${LOG_DIR}/pipeline.log' 2>&1"
  # Weekly, and never unattended-destructive: --dry-run reports what would be
  # removed so the retention windows can be reviewed before anything is deleted.
  local archive_cmd="0 ${ARCHIVE_HOUR} * * ${ARCHIVE_DAY} cd '${REPO_DIR}' && ${ARCHIVE_RUN} >> '${LOG_DIR}/archive.log' 2>&1"
  # Daily, an hour after the pipeline starts. Knowledge stories cost a local
  # LLM call each and cannot be regenerated once their source articles age out.
  local backup_cmd="${BACKUP_MINUTE} ${BACKUP_HOUR} * * * cd '${REPO_DIR}' && ${BACKUP_RUN} >> '${LOG_DIR}/backup.log' 2>&1"

  # `|| true` is load-bearing under `set -euo pipefail`: `crontab -l` exits 1
  # when the user has no crontab yet, and each `grep -v` exits 1 when it filters
  # everything out. Either would abort the install with no output at all —
  # precisely the first-run case this script exists for.
  {
    { crontab -l 2>/dev/null \
        | grep -v "${marker}" \
        | grep -v "run_pipeline.py" \
        | grep -v "archive.py" \
        | grep -v "backup_db.sh" \
        | grep -v "^CRON_TZ="; } || true
    if [[ -n "${SCHED_TZ}" ]]; then
      echo "${marker} ${ORIG_TIME} ${SCHED_TZ} = ${HOUR}:${MINUTE} $(date +%Z) (converted; this cron has no CRON_TZ)"
    fi
    echo "${marker} daily pipeline"
    echo "${pipeline_cmd}"
    if [[ "${WITH_ARCHIVE}" == true ]]; then
      echo "${marker} weekly archive report"
      echo "${archive_cmd}"
    fi
    if [[ "${WITH_BACKUP}" == true ]]; then
      echo "${marker} daily database backup"
      echo "${backup_cmd}"
    fi
  } | crontab -

  echo "Cron installed."
  echo "  Interpreter: ${PYTHON}"
  if [[ -n "${SCHED_TZ}" ]]; then
    echo "  Requested:   ${ORIG_TIME} ${SCHED_TZ}"
    echo "  Converted:   ${HOUR}:${MINUTE} $(date +%Z) — cron uses local time and"
    echo "               this build has no CRON_TZ support"
  fi
  echo "  Pipeline:    daily at ${HOUR}:${MINUTE}   -> ${LOG_DIR}/pipeline.log"
  if [[ "${WITH_ARCHIVE}" == true ]]; then
    echo "  Archive:     Sundays at ${ARCHIVE_HOUR}:00 (DRY RUN) -> ${LOG_DIR}/archive.log"
    echo ""
    echo "  The archive entry only REPORTS. Review ${LOG_DIR}/archive.log, then run"
    echo "  'python scripts/archive.py' by hand to actually reclaim space."
  fi
  if [[ "${WITH_BACKUP}" == true ]]; then
    echo "  Backup:      daily at ${BACKUP_HOUR}:${BACKUP_MINUTE} -> ${LOG_DIR}/backup.log"
    echo "               dumps to ~/briefing-backups, keeping the newest 14"
  fi
  echo ""
  echo "To check:  crontab -l | grep -E 'intelligence|run_pipeline|archive.py|backup_db'"
  echo "To remove: ./scripts/install_cron.sh --uninstall"
}

# ── Uninstall ────────────────────────────────────────────────────────────────
uninstall_cron() {
  # Must match the COMMAND lines, not just the marker comments. The marker
  # appears only on the comment above each entry, so filtering on it alone
  # strips the labels and leaves all three jobs running — unlabelled, and
  # therefore harder to find next time.
  { crontab -l 2>/dev/null \
      | grep -v "# intelligence-briefing" \
      | grep -v "run_pipeline.py" \
      | grep -v "archive.py" \
      | grep -v "backup_db.sh" \
      | grep -v "^CRON_TZ="; } | crontab - || true

  local left
  left="$(crontab -l 2>/dev/null | grep -cE 'run_pipeline\.py|archive\.py|backup_db\.sh' || true)"
  if [[ "${left:-0}" -eq 0 ]]; then
    echo "Cron entries removed. Other crontab entries were left untouched."
  else
    echo "warning: ${left} briefing entr(y|ies) still present — check 'crontab -l'" >&2
    exit 1
  fi
}

# ── Systemd install ──────────────────────────────────────────────────────────
install_systemd() {
  local SERVICE_NAME="intelligence-briefing"
  local SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
  local TIMER_FILE="/etc/systemd/system/${SERVICE_NAME}.timer"
  local CURRENT_USER="${SUDO_USER:-$USER}"

  cat > "${SERVICE_FILE}" << EOF
[Unit]
Description=Intelligence Briefing — daily pipeline
After=network.target postgresql.service

[Service]
Type=oneshot
User=${CURRENT_USER}
WorkingDirectory=${REPO_DIR}
ExecStart=${PYTHON} scripts/run_pipeline.py
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

  cat > "${TIMER_FILE}" << EOF
[Unit]
Description=Run Intelligence Briefing pipeline daily at ${HOUR}:${MINUTE}
Requires=${SERVICE_NAME}.service

[Timer]
OnCalendar=*-*-* ${HOUR}:${MINUTE}:00
Persistent=true

[Install]
WantedBy=timers.target
EOF

  if [[ "${WITH_ARCHIVE}" == true ]]; then
    # Reports only — see the note in install_cron() for why this is not a
    # live delete.
    cat > "/etc/systemd/system/${SERVICE_NAME}-archive.service" << EOF
[Unit]
Description=Intelligence Briefing — weekly retention report
After=network.target postgresql.service

[Service]
Type=oneshot
User=${CURRENT_USER}
WorkingDirectory=${REPO_DIR}
ExecStart=${PYTHON} scripts/archive.py --dry-run
StandardOutput=journal
StandardError=journal
EOF

    cat > "/etc/systemd/system/${SERVICE_NAME}-archive.timer" << EOF
[Unit]
Description=Weekly Intelligence Briefing retention report
Requires=${SERVICE_NAME}-archive.service

[Timer]
OnCalendar=Sun *-*-* ${ARCHIVE_HOUR}:00:00
Persistent=true

[Install]
WantedBy=timers.target
EOF
  fi

  if [[ "${WITH_BACKUP}" == true ]]; then
    cat > "/etc/systemd/system/${SERVICE_NAME}-backup.service" << EOF
[Unit]
Description=Intelligence Briefing — daily database backup
After=network.target postgresql.service

[Service]
Type=oneshot
User=${CURRENT_USER}
WorkingDirectory=${REPO_DIR}
ExecStart=${REPO_DIR}/scripts/backup_db.sh
StandardOutput=journal
StandardError=journal
EOF

    cat > "/etc/systemd/system/${SERVICE_NAME}-backup.timer" << EOF
[Unit]
Description=Daily Intelligence Briefing database backup
Requires=${SERVICE_NAME}-backup.service

[Timer]
OnCalendar=*-*-* ${BACKUP_HOUR}:${BACKUP_MINUTE}:00
Persistent=true

[Install]
WantedBy=timers.target
EOF
  fi

  systemctl daemon-reload
  systemctl enable "${SERVICE_NAME}.timer"
  systemctl start  "${SERVICE_NAME}.timer"
  if [[ "${WITH_ARCHIVE}" == true ]]; then
    systemctl enable "${SERVICE_NAME}-archive.timer"
    systemctl start  "${SERVICE_NAME}-archive.timer"
  fi
  if [[ "${WITH_BACKUP}" == true ]]; then
    systemctl enable "${SERVICE_NAME}-backup.timer"
    systemctl start  "${SERVICE_NAME}-backup.timer"
  fi

  echo "Systemd timers installed:"
  echo "  Interpreter: ${PYTHON}"
  echo "  Service:     ${SERVICE_FILE}"
  echo "  Timer:       ${TIMER_FILE}"
  echo "  Schedule:    daily at ${HOUR}:${MINUTE}"
  if [[ "${WITH_ARCHIVE}" == true ]]; then
    echo "  Archive:     Sundays at ${ARCHIVE_HOUR}:00 (DRY RUN, reports to journal)"
  fi
  if [[ "${WITH_BACKUP}" == true ]]; then
    echo "  Backup:      daily at ${BACKUP_HOUR}:${BACKUP_MINUTE} -> ~/briefing-backups"
  fi
  echo ""
  echo "Check status:    systemctl status ${SERVICE_NAME}.timer"
  echo "View logs:       journalctl -u ${SERVICE_NAME}.service -f"
  echo "Run now:         systemctl start ${SERVICE_NAME}.service"
  echo "Disable:         systemctl disable ${SERVICE_NAME}.timer"
}

# ── Run ──────────────────────────────────────────────────────────────────────
if [[ "${DO_UNINSTALL}" == true ]]; then
  uninstall_cron
  exit 0
fi

if [[ "${USE_SYSTEMD}" == true ]]; then
  if [[ "${EUID}" -ne 0 ]]; then
    echo "Systemd install requires sudo: sudo $0 --systemd"
    exit 1
  fi
  install_systemd
else
  install_cron
fi
