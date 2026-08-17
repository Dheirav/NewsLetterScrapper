#!/usr/bin/env bash
# scripts/backup_db.sh
# ─────────────────────────────────────────────────────────────────────────────
# Dump the database to a gzipped, dated file and prune old copies.
#
# Usage:
#   ./scripts/backup_db.sh                  # dump to ~/briefing-backups
#   ./scripts/backup_db.sh --dir /mnt/nas   # somewhere else
#   ./scripts/backup_db.sh --keep 30        # retain 30 dumps instead of 14
#
# The knowledge stories are the expensive artefact here: each one costs a local
# LLM call and cannot be regenerated once the source articles age out, so this
# is the only copy that survives losing the machine.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-${HOME}/briefing-backups}"
KEEP="${KEEP:-14}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir)  BACKUP_DIR="$2"; shift 2 ;;
    --keep) KEEP="$2";       shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

if [[ ! -f "${REPO_DIR}/.env" ]]; then
  echo "error: ${REPO_DIR}/.env not found — cannot read DATABASE_URL" >&2
  exit 1
fi

# asyncpg's driver prefix is not understood by libpq.
DB_URL="$(grep '^DATABASE_URL=' "${REPO_DIR}/.env" | cut -d= -f2- | sed 's|postgresql+asyncpg|postgresql|')"
if [[ -z "${DB_URL}" ]]; then
  echo "error: DATABASE_URL is empty in ${REPO_DIR}/.env" >&2
  exit 1
fi

mkdir -p "${BACKUP_DIR}"
STAMP="$(date +%Y%m%d-%H%M)"
TARGET="${BACKUP_DIR}/intelligence_db-${STAMP}.sql.gz"

# Write to a temp name first: a half-written file that later looks like a valid
# backup is worse than no backup, and pruning below must never see one.
TMP="${TARGET}.partial"
trap 'rm -f "${TMP}"' EXIT

if ! pg_dump "${DB_URL}" | gzip > "${TMP}"; then
  echo "error: pg_dump failed — keeping existing backups untouched" >&2
  exit 1
fi

# gzip -t verifies the stream is complete; a truncated dump fails here.
if ! gzip -t "${TMP}" 2>/dev/null; then
  echo "error: dump did not gzip cleanly — discarding, keeping existing backups" >&2
  exit 1
fi

mv "${TMP}" "${TARGET}"
trap - EXIT

SIZE="$(du -h "${TARGET}" | cut -f1)"
echo "$(date '+%Y-%m-%d %H:%M:%S')  backup ok  ${TARGET}  (${SIZE})"

# Prune only after a verified success, so a run of failures can never leave
# you with nothing.
#
# Sorted by FILENAME, not mtime. The names embed YYYYMMDD-HHMM so they sort
# chronologically on their own, and that stays true after the files are copied
# to another disk or restored from elsewhere — both of which rewrite mtime and
# would otherwise make this delete the wrong ones.
mapfile -t OLD < <(ls -1 "${BACKUP_DIR}"/intelligence_db-*.sql.gz 2>/dev/null | sort -r | tail -n +$((KEEP + 1)))
for f in "${OLD[@]:-}"; do
  [[ -n "$f" ]] || continue
  rm -f "$f"
  echo "$(date '+%Y-%m-%d %H:%M:%S')  pruned     $f"
done

COUNT="$(ls -1 "${BACKUP_DIR}"/intelligence_db-*.sql.gz 2>/dev/null | wc -l)"
echo "$(date '+%Y-%m-%d %H:%M:%S')  retained   ${COUNT} backup(s), keeping ${KEEP}"
