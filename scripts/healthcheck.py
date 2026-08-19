"""
scripts/healthcheck.py
-----------------------
Detect a pipeline run that did not produce a briefing, and say why.

Every failure this project has actually had was silent. A model named in
OLLAMA_LLM_MODEL but not installed makes every LLM call raise; labeler.py
catches per cluster and generator.py dead-letters each failure, so the run
completes in twenty minutes with exit code 0, generates nothing, and sends no
email. Nothing anywhere reports a problem. Unattended, that is indistinguishable
from working.

This runs a few hours after the pipeline and emails the operator when the day's
briefing is missing or incomplete, with the specific reason attached.

Usage:
    python scripts/healthcheck.py                 # check today, alert if unhealthy
    python scripts/healthcheck.py --date 2026-08-17
    python scripts/healthcheck.py --dry-run       # report only, never email
    python scripts/healthcheck.py --force-alert   # email even when healthy
                                                  # (proves delivery works)

Exit codes: 0 healthy, 1 problem found, 2 the check itself could not run.
"""
import argparse
import asyncio
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from email.message import EmailMessage

from sqlalchemy import func, select

from core.config import settings

# ── Findings ─────────────────────────────────────────────────────────────────

OK, WARN, FAIL = "ok", "warn", "fail"


@dataclass
class Check:
    name: str
    status: str
    detail: str
    fix: str = ""


@dataclass
class Report:
    target: date
    checks: list[Check] = field(default_factory=list)

    def add(self, name, status, detail, fix=""):
        self.checks.append(Check(name, status, detail, fix))

    @property
    def worst(self) -> str:
        if any(c.status == FAIL for c in self.checks):
            return FAIL
        if any(c.status == WARN for c in self.checks):
            return WARN
        return OK

    @property
    def healthy(self) -> bool:
        return self.worst == OK

    def as_text(self) -> str:
        icon = {OK: "ok  ", WARN: "WARN", FAIL: "FAIL"}
        lines = [
            f"Intelligence Briefing health check — {self.target}",
            f"Overall: {self.worst.upper()}",
            "",
        ]
        for c in self.checks:
            lines.append(f"[{icon[c.status]}] {c.name}")
            lines.append(f"         {c.detail}")
            if c.fix and c.status != OK:
                lines.append(f"         fix: {c.fix}")
        problems = [c for c in self.checks if c.status != OK]
        if problems:
            lines += ["", "Logs:", "  tail -50 logs/pipeline.log"]
        return "\n".join(lines)


# ── Checks ───────────────────────────────────────────────────────────────────

def check_model_installed(report: Report) -> None:
    """
    The silent killer. Verified before anything else because it explains a run
    that completed cleanly and produced nothing.
    """
    want = settings.ollama_llm_model
    try:
        import ollama
        names = [m.get("model") or m.get("name") or "" for m in ollama.list().get("models", [])]
    except Exception as exc:
        report.add("ollama reachable", FAIL, f"cannot reach Ollama: {str(exc)[:90]}",
                   "is the ollama service running?")
        return

    # `llama3` should match `llama3:latest`.
    if any(n == want or n.startswith(f"{want}:") for n in names):
        report.add("ollama model", OK, f"{want} is installed")
    else:
        report.add("ollama model", FAIL,
                   f"OLLAMA_LLM_MODEL={want} is NOT installed (have: {', '.join(names) or 'none'})",
                   f"ollama pull {want}")


async def check_database(report: Report, target: date) -> None:
    from core.db.orm_models import (
        FailedGenerationORM, KnowledgeStoryORM, NewsletterORM, PipelineRunORM,
    )
    from core.db.session import get_session

    async with get_session() as s:
        nl = (await s.execute(
            select(NewsletterORM).where(NewsletterORM.newsletter_date == target)
        )).scalar_one_or_none()

        stories = (await s.execute(
            select(func.count(KnowledgeStoryORM.id))
            .where(KnowledgeStoryORM.story_date == target)
        )).scalar() or 0

        steps = [r[0] for r in (await s.execute(
            select(PipelineRunORM.step_name)
            .where(PipelineRunORM.run_date == target, PipelineRunORM.status == "completed")
        )).fetchall()]

        failed = (await s.execute(
            select(func.count(FailedGenerationORM.id))
            .where(FailedGenerationORM.run_date == target)
        )).scalar() or 0

    if not steps and not nl:
        report.add("pipeline ran", FAIL, f"no trace of a run on {target}",
                   "cron did not fire, or the machine was asleep — check `crontab -l`")
    else:
        report.add("pipeline ran", OK, f"steps recorded: {', '.join(steps) or 'none'}")

    if stories == 0:
        report.add("stories generated", FAIL, f"0 knowledge stories for {target}",
                   "usually the Ollama model check above; otherwise see failed_generations")
    else:
        report.add("stories generated", OK, f"{stories} stories")

    if nl is None:
        report.add("newsletter built", FAIL, "no newsletter row for this date",
                   "assembly aborts when no stories were generated")
    elif not (nl.html_content or "").strip():
        report.add("newsletter built", WARN, "newsletter row exists but HTML is empty",
                   "archive.py strips HTML past the retention window — expected for old dates")
    else:
        report.add("newsletter built", OK, f"{len(nl.html_content)} chars rendered")

    if nl is not None:
        if nl.sent:
            when = nl.sent_at.strftime("%H:%M") if nl.sent_at else "unknown time"
            report.add("email sent", OK, f"marked sent at {when}")
        else:
            report.add("email sent", FAIL, "newsletter was built but never marked sent",
                       "SMTP failure, or the run was --test (which never marks sent)")

    if failed:
        report.add("generation failures", WARN, f"{failed} clusters in the dead-letter queue",
                   "python scripts/explore_db.py --stats")
    else:
        report.add("generation failures", OK, "none")


# ── Alerting ─────────────────────────────────────────────────────────────────

async def send_alert(report: Report) -> bool:
    """
    Plain SMTP, deliberately not services.newsletter.emailer.send_email.

    That path filters opted-out addresses and injects unsubscribe links — both
    correct for a briefing and wrong for an operational alert, which must reach
    the operator whatever the unsubscribe table says.
    """
    import aiosmtplib

    to = settings.alert_recipient
    if not to:
        print("no ALERT_EMAIL / TEST_EMAIL / SMTP_USER configured — cannot alert", file=sys.stderr)
        return False

    msg = EmailMessage()
    msg["Subject"] = f"[{report.worst.upper()}] Intelligence Briefing — {report.target}"
    msg["From"] = f"Briefing Health <{settings.smtp_user}>"
    msg["To"] = to
    msg.set_content(report.as_text())

    try:
        await aiosmtplib.send(
            msg, hostname=settings.smtp_host, port=settings.smtp_port,
            username=settings.smtp_user, password=settings.smtp_password,
            start_tls=True,
        )
        return True
    except Exception as exc:
        print(f"alert delivery failed: {exc}", file=sys.stderr)
        return False


async def main(target: date, dry_run: bool, force_alert: bool) -> int:
    report = Report(target=target)
    check_model_installed(report)
    try:
        await check_database(report, target)
    except Exception as exc:
        report.add("database", FAIL, f"cannot query the database: {str(exc)[:110]}",
                   "is Postgres running?")

    print(report.as_text())

    if dry_run:
        print("\n[dry run — no alert sent]")
    elif not report.healthy or force_alert:
        ok = await send_alert(report)
        print(f"\nalert {'sent to ' + settings.alert_recipient if ok else 'FAILED to send'}")
        if not ok:
            return 2

    return 0 if report.healthy else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Report whether today's briefing was produced")
    ap.add_argument("--date", type=date.fromisoformat, default=date.today(), metavar="YYYY-MM-DD")
    ap.add_argument("--dry-run", action="store_true", help="report only, never email")
    ap.add_argument("--force-alert", action="store_true",
                    help="email even when healthy, to prove delivery works")
    a = ap.parse_args()
    sys.exit(asyncio.run(main(a.date, a.dry_run, a.force_alert)))
