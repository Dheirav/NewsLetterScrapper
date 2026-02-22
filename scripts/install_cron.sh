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
PYTHON="${PYTHON:-$(which python3)}"
HOUR="06"
MINUTE="00"
USE_SYSTEMD=false

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
    *)
      echo "Unknown argument: $1"
      exit 1
      ;;
  esac
done

# ── Cron install ─────────────────────────────────────────────────────────────
install_cron() {
  local cron_cmd="${MINUTE} ${HOUR} * * * cd '${REPO_DIR}' && '${PYTHON}' scripts/run_pipeline.py >> /var/log/intelligence-briefing.log 2>&1"
  local marker="# intelligence-briefing daily pipeline"

  # Remove any previous entry, then append the fresh one
  (crontab -l 2>/dev/null | grep -v "${marker}"; echo "${marker}"; echo "${cron_cmd}") | crontab -

  echo "Cron job installed — pipeline runs daily at ${HOUR}:${MINUTE}"
  echo "Logs: /var/log/intelligence-briefing.log"
  echo ""
  echo "To check: crontab -l | grep intelligence"
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

  systemctl daemon-reload
  systemctl enable "${SERVICE_NAME}.timer"
  systemctl start  "${SERVICE_NAME}.timer"

  echo "Systemd timer installed:"
  echo "  Service:  ${SERVICE_FILE}"
  echo "  Timer:    ${TIMER_FILE}"
  echo "  Schedule: daily at ${HOUR}:${MINUTE}"
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
