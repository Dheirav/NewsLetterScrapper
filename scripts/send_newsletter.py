"""
scripts/send_newsletter.py
---------------------------
Send an already-rendered newsletter from the database without running
the full pipeline.

Usage:
    python scripts/send_newsletter.py                   # today's newsletter
    python scripts/send_newsletter.py --date 2026-02-21 # specific date
    python scripts/send_newsletter.py --to extra@example.com        # override recipients
    python scripts/send_newsletter.py --to a@x.com --to b@x.com     # multiple overrides
"""
import argparse
import asyncio
import logging
import logging.config
from datetime import date

from core.config import settings
from core.db.session import get_session
from services.newsletter.emailer import send_email
from services.newsletter.repository import get_newsletter_for_date, mark_sent

logging.basicConfig(
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
    level=settings.log_level.upper(),
)
log = logging.getLogger("send_newsletter")


async def main(target_date: date, recipients: list[str] | None) -> None:
    async with get_session() as session:
        orm = await get_newsletter_for_date(target_date, session)

        if orm is None:
            log.error(
                "No newsletter found for %s — run the pipeline first:\n"
                "  python scripts/run_pipeline.py",
                target_date,
            )
            raise SystemExit(1)

        if orm.sent and not recipients:
            log.warning(
                "Newsletter for %s was already sent at %s. "
                "Pass --to <address> to override recipients and resend.",
                target_date,
                orm.sent_at,
            )

        subject = f"Intelligence Briefing — {target_date.strftime('%B %d, %Y')}"
        log.info("Sending newsletter for %s …", target_date)

        sent = await send_email(
            html_content=orm.html_content,
            subject=subject,
            recipients=recipients or None,  # None → falls back to settings.all_recipients
        )

        if sent:
            if not recipients:
                # Only mark as sent when using the default recipient list
                await mark_sent(orm.id, session)
            log.info("Done — newsletter sent successfully.")
        else:
            log.error("Send failed — check SMTP settings in .env")
            raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Send a previously rendered newsletter from the database"
    )
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        default=date.today(),
        metavar="YYYY-MM-DD",
        help="Date of the newsletter to send (default: today)",
    )
    parser.add_argument(
        "--to",
        dest="recipients",
        action="append",
        default=[],
        metavar="EMAIL",
        help="Override recipient(s). Can be specified multiple times.",
    )
    args = parser.parse_args()

    asyncio.run(main(args.date, args.recipients or None))
