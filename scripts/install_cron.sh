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
USE_SYSTEMD=false
WITH_ARCHIVE=true

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
    *)
      echo "Unknown argument: $1"
      exit 1
      ;;
  esac
done

# ── Cron install ─────────────────────────────────────────────────────────────
install_cron() {
  mkdir -p "${LOG_DIR}"

  local marker="# intelligence-briefing"
  local pipeline_cmd="${MINUTE} ${HOUR} * * * cd '${REPO_DIR}' && '${PYTHON}' scripts/run_pipeline.py >> '${LOG_DIR}/pipeline.log' 2>&1"
  # Weekly, and never unattended-destructive: --dry-run reports what would be
  # removed so the retention windows can be reviewed before anything is deleted.
  local archive_cmd="0 ${ARCHIVE_HOUR} * * ${ARCHIVE_DAY} cd '${REPO_DIR}' && '${PYTHON}' scripts/archive.py --dry-run >> '${LOG_DIR}/archive.log' 2>&1"

  {
    crontab -l 2>/dev/null | grep -v "${marker}" | grep -v "run_pipeline.py" | grep -v "archive.py"
    echo "${marker} daily pipeline"
    echo "${pipeline_cmd}"
    if [[ "${WITH_ARCHIVE}" == true ]]; then
      echo "${marker} weekly archive report"
      echo "${archive_cmd}"
    fi
  } | crontab -

  echo "Cron installed."
  echo "  Interpreter: ${PYTHON}"
  echo "  Pipeline:    daily at ${HOUR}:${MINUTE}   -> ${LOG_DIR}/pipeline.log"
  if [[ "${WITH_ARCHIVE}" == true ]]; then
    echo "  Archive:     Sundays at ${ARCHIVE_HOUR}:00 (DRY RUN) -> ${LOG_DIR}/archive.log"
    echo ""
    echo "  The archive entry only REPORTS. Review ${LOG_DIR}/archive.log, then run"
    echo "  'python scripts/archive.py' by hand to actually reclaim space."
  fi
  echo ""
  echo "To check:  crontab -l | grep intelligence"
  echo "To remove: crontab -l | grep -v intelligence-briefing | crontab -"
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

  systemctl daemon-reload
  systemctl enable "${SERVICE_NAME}.timer"
  systemctl start  "${SERVICE_NAME}.timer"
  if [[ "${WITH_ARCHIVE}" == true ]]; then
    systemctl enable "${SERVICE_NAME}-archive.timer"
    systemctl start  "${SERVICE_NAME}-archive.timer"
  fi

  echo "Systemd timers installed:"
  echo "  Interpreter: ${PYTHON}"
  echo "  Service:     ${SERVICE_FILE}"
  echo "  Timer:       ${TIMER_FILE}"
  echo "  Schedule:    daily at ${HOUR}:${MINUTE}"
  if [[ "${WITH_ARCHIVE}" == true ]]; then
    echo "  Archive:     Sundays at ${ARCHIVE_HOUR}:00 (DRY RUN, reports to journal)"
  fi
  echo ""
  echo "Check status:    systemctl status ${SERVICE_NAME}.timer"
  echo "View logs:       journalctl -u ${SERVICE_NAME}.service -f"
  echo "Run now:         systemctl start ${SERVICE_NAME}.service"
  echo "Disable:         systemctl disable ${SERVICE_NAME}.timer"
}

# ── Run ──────────────────────────────────────────────────────────────────────
if [[ "${USE_SYSTEMD}" == true ]]; then
  if [[ "${EUID}" -ne 0 ]]; then
    echo "Systemd install requires sudo: sudo $0 --systemd"
    exit 1
  fi
  install_systemd
else
  install_cron
fi
