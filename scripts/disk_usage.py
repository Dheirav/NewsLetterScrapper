"""
scripts/disk_usage.py
----------------------
Report how much disk space this project consumes, broken into three buckets:

  1. CODE   — Python source, templates, configs (the repo itself, excl. artifacts)
  2. MODELS — Ollama LLM & embedding models stored locally
  3. DATA   — PostgreSQL tables (rows + indexes, per table)

Usage:
    python scripts/disk_usage.py            # full report
    python scripts/disk_usage.py --json     # machine-readable JSON output
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import httpx
from sqlalchemy import text

# ── project root is two levels up from this file ─────────────────────────────
ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(ROOT))

from core.config import settings
from core.db.session import get_session

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

# Directories / patterns inside the repo that are NOT "source code"
_SKIP_DIRS = {
    "__pycache__",
    ".git",
    ".venv",
    "venv",
    "env",
    ".env",
    "node_modules",
    "newsletter_scrapper.egg-info",
    ".mypy_cache",
    ".pytest_cache",
    "dist",
    "build",
}


def _fmt(n_bytes: int) -> str:
    """Human-readable byte count."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n_bytes) < 1024:
            return f"{n_bytes:,.1f} {unit}"
        n_bytes /= 1024
    return f"{n_bytes:,.1f} PB"


def _bar(fraction: float, width: int = 30) -> str:
    filled = round(fraction * width)
    return "█" * filled + "░" * (width - filled)


# ─────────────────────────────────────────────────────────────────────────────
# 1. CODE
# ─────────────────────────────────────────────────────────────────────────────

_CODE_EXTS = {
    ".py", ".html", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".md", ".txt", ".sh", ".env", ".sql", ".mako",
}


def measure_code() -> dict:
    """Walk the repo and tally source-file sizes by category."""
    totals: dict[str, int] = {"python": 0, "templates": 0, "config": 0, "other": 0}
    file_count = 0

    for path in ROOT.rglob("*"):
        # skip non-files and excluded dirs
        if not path.is_file():
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if path.suffix not in _CODE_EXTS:
            continue

        size = path.stat().st_size
        file_count += 1

        rel = path.relative_to(ROOT)
        parts = rel.parts
        if path.suffix == ".py":
            totals["python"] += size
        elif path.suffix in (".html",):
            totals["templates"] += size
        elif parts[0] in ("core", "migrations") or path.suffix in (".toml", ".ini", ".cfg", ".yaml", ".yml", ".env", ".sh"):
            totals["config"] += size
        else:
            totals["other"] += size

    total = sum(totals.values())
    return {"breakdown": totals, "total": total, "file_count": file_count}


# ─────────────────────────────────────────────────────────────────────────────
# 2. MODELS  (Ollama)
# ─────────────────────────────────────────────────────────────────────────────

def measure_models() -> dict:
    """Query the Ollama API for installed model sizes."""
    models = []
    total = 0
    error = None

    try:
        resp = httpx.get(f"{settings.ollama_base_url}/api/tags", timeout=5)
        resp.raise_for_status()
        data = resp.json()
        for m in data.get("models", []):
            size = m.get("size", 0)
            models.append({"name": m["name"], "size": size})
            total += size
    except Exception as exc:
        error = str(exc)

    # Also check ~/.ollama/models on disk as a fallback / cross-check
    ollama_dir = Path.home() / ".ollama" / "models"
    disk_total = 0
    if ollama_dir.exists():
        for p in ollama_dir.rglob("*"):
            if p.is_file():
                disk_total += p.stat().st_size

    return {
        "api_models": models,
        "api_total": total,
        "disk_total": disk_total,
        "error": error,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 3. DATA  (PostgreSQL)
# ─────────────────────────────────────────────────────────────────────────────

async def measure_data() -> dict:
    """Query pg_catalog for per-table and total DB sizes."""
    tables = []
    db_total = 0
    error = None

    try:
        async with get_session() as session:
            # Total database size
            row = await session.execute(
                text("SELECT pg_database_size(current_database())")
            )
            db_total = row.scalar()

            # Per-table sizes (data + indexes, plus toast)
            rows = await session.execute(text("""
                SELECT
                    relname                          AS table_name,
                    pg_total_relation_size(oid)      AS total_bytes,
                    pg_relation_size(oid)            AS data_bytes,
                    pg_indexes_size(oid)             AS index_bytes,
                    (SELECT count(*) FROM pg_class c2
                     WHERE c2.reltoastrelid = pg_class.oid) AS has_toast,
                    pg_total_relation_size(reltoastrelid)   AS toast_bytes
                FROM pg_class
                WHERE relkind = 'r'
                  AND relnamespace = (
                      SELECT oid FROM pg_namespace WHERE nspname = 'public'
                  )
                ORDER BY total_bytes DESC
            """))
            for r in rows:
                tables.append({
                    "table": r.table_name,
                    "total": r.total_bytes or 0,
                    "data": r.data_bytes or 0,
                    "indexes": r.index_bytes or 0,
                    "toast": r.toast_bytes or 0,
                })
    except Exception as exc:
        error = str(exc)

    return {"tables": tables, "db_total": db_total, "error": error}


# ─────────────────────────────────────────────────────────────────────────────
# Report rendering
# ─────────────────────────────────────────────────────────────────────────────

_W = 64  # total width of the report box

def _hdr(title: str) -> str:
    pad = _W - 4 - len(title)
    return f"\n  ┌{'─' * (_W - 2)}┐\n  │  {title}{' ' * pad}  │\n  └{'─' * (_W - 2)}┘"


def _row(label: str, value: str, note: str = "") -> str:
    note_str = f"  {note}" if note else ""
    return f"    {label:<28} {value:>12}{note_str}"


def print_report(code: dict, models: dict, data: dict) -> None:
    code_total   = code["total"]
    models_total = models["disk_total"] or models["api_total"]
    data_total   = data["db_total"] or 0
    grand_total  = code_total + models_total + data_total

    # ── header ────────────────────────────────────────────────────────────────
    print()
    print("  " + "═" * (_W - 2))
    print(f"  {'INTELLIGENCE BRIEFING — DISK USAGE REPORT':^{_W - 2}}")
    print("  " + "═" * (_W - 2))
    print()

    # ── grand total bar ───────────────────────────────────────────────────────
    if grand_total > 0:
        cf = code_total / grand_total
        mf = models_total / grand_total
        df = data_total / grand_total
        BW = 50
        cb = round(cf * BW)
        mb = round(mf * BW)
        db_b = BW - cb - mb
        bar = f"  \033[34m{'█' * cb}\033[33m{'█' * mb}\033[32m{'█' * db_b}\033[0m"
        print(f"  Total: {_fmt(grand_total)}")
        print(f"  {bar}  \033[34mcode\033[0m / \033[33mmodels\033[0m / \033[32mdata\033[0m")
        print()

    # ── 1. CODE ───────────────────────────────────────────────────────────────
    print(_hdr("1 · CODE"))
    bd = code["breakdown"]
    print(_row("Python source (.py)", _fmt(bd["python"])))
    print(_row("Templates (.html)", _fmt(bd["templates"])))
    print(_row("Config & migrations", _fmt(bd["config"])))
    print(_row("Other tracked files", _fmt(bd["other"])))
    print()
    print(_row("── TOTAL  CODE", _fmt(code_total),
               f"({code['file_count']} files)"))
    print()

    # ── 2. MODELS ─────────────────────────────────────────────────────────────
    print(_hdr("2 · MODELS  (Ollama)"))
    if models["error"]:
        print(f"    ⚠  Ollama API unreachable: {models['error']}")
    if models["api_models"]:
        for m in models["api_models"]:
            tag = f"  {'(active)' if m['name'] in (settings.ollama_llm_model, settings.ollama_embed_model) else ''}"
            print(_row(m["name"][:28], _fmt(m["size"]), tag.strip()))
    elif not models["error"]:
        print("    (no models returned by API)")
    print()
    label = "disk scan" if not models["api_total"] else "API reported"
    print(_row(f"── TOTAL  MODELS  ({label})", _fmt(models_total)))
    print()

    # ── 3. DATA ───────────────────────────────────────────────────────────────
    print(_hdr("3 · DATA  (PostgreSQL)"))
    if data["error"]:
        print(f"    ⚠  DB unreachable: {data['error']}")
    if data["tables"]:
        print(f"    {'Table':<28} {'Total':>10}  {'Data':>10}  {'Indexes':>10}")
        print(f"    {'─' * 28} {'─' * 10}  {'─' * 10}  {'─' * 10}")
        for t in data["tables"]:
            print(f"    {t['table']:<28} {_fmt(t['total']):>10}  {_fmt(t['data']):>10}  {_fmt(t['indexes']):>10}")
    print()
    print(_row("── TOTAL  DATABASE", _fmt(data_total)))
    print()

    # ── summary ───────────────────────────────────────────────────────────────
    print("  " + "─" * (_W - 2))
    pct = lambda n: f"({n / grand_total * 100:.1f}%)" if grand_total else ""
    print(_row("Code",   _fmt(code_total),   pct(code_total)))
    print(_row("Models", _fmt(models_total), pct(models_total)))
    print(_row("Data",   _fmt(data_total),   pct(data_total)))
    print("  " + "─" * (_W - 2))
    print(_row("GRAND TOTAL", _fmt(grand_total)))
    print()


def print_json(code: dict, models: dict, data: dict) -> None:
    out = {
        "code": code,
        "models": models,
        "data": data,
        "summary": {
            "code_bytes":   code["total"],
            "models_bytes": models["disk_total"] or models["api_total"],
            "data_bytes":   data["db_total"] or 0,
        },
    }
    out["summary"]["grand_total_bytes"] = sum(out["summary"].values())
    print(json.dumps(out, indent=2, default=str))


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

async def main(as_json: bool) -> None:
    code   = measure_code()
    models = measure_models()
    data   = await measure_data()

    if as_json:
        print_json(code, models, data)
    else:
        print_report(code, models, data)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Show disk usage for the Intelligence Briefing project.")
    parser.add_argument("--json", action="store_true", help="Output as JSON instead of a human-readable report.")
    args = parser.parse_args()
    asyncio.run(main(args.json))
