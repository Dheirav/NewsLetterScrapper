"""
scripts/send_newsletter.py
---------------------------
Send an already-rendered newsletter from the database without running
the full pipeline.

Usage:
    python scripts/send_newsletter.py                        # today's newsletter
    python scripts/send_newsletter.py --date 2026-02-21      # specific date
    python scripts/send_newsletter.py --to extra@example.com # override recipients
    python scripts/send_newsletter.py --to a@x.com --to b@x.com   # multiple overrides
    python scripts/send_newsletter.py --rerender             # re-render email HTML before sending
    python scripts/send_newsletter.py --test                 # send only to TEST_EMAIL (never marks as sent)
"""
import argparse
import asyncio
import logging
import logging.config
from datetime import date

from core.config import settings
from core.db.session import get_session
from services.newsletter.assembler import assemble
from services.newsletter.emailer import send_email
from services.newsletter.renderer import render_email_html
from services.newsletter.repository import get_newsletter_for_date, mark_sent
from services.knowledge.repository import get_stories_for_date

logging.basicConfig(
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
    level=settings.log_level.upper(),
)
log = logging.getLogger("send_newsletter")


async def main(target_date: date, recipients: list[str] | None, rerender: bool = False, test: bool = False) -> None:
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

        # Re-render email HTML from stored stories if requested or if the
        # email version hasn't been rendered yet (legacy record).
        if rerender or not orm.email_html_content:
            log.info("Re-rendering email HTML for %s …", target_date)
            stories = await get_stories_for_date(target_date, session)
            if not stories:
                log.error(
                    "No knowledge stories found for %s — cannot re-render.",
                    target_date,
                )
                raise SystemExit(1)
            newsletter = assemble(stories, target_date)
            orm.email_html_content = render_email_html(newsletter)
            log.info(
                "Email HTML re-rendered (%d chars) and saved.",
                len(orm.email_html_content),
            )

        subject = f"Intelligence Briefing — {target_date.strftime('%B %d, %Y')}"

        if test:
            if not settings.test_email:
                log.error(
                    "--test requires TEST_EMAIL to be set in .env"
                )
                raise SystemExit(1)
            effective_recipients: list[str] | None = [settings.test_email]
            log.info(
                "TEST MODE — sending only to %s (newsletter will NOT be marked as sent)",
                settings.test_email,
            )
        else:
            effective_recipients = recipients or None

        log.info("Sending newsletter for %s …", target_date)

        html_to_send = orm.email_html_content or orm.html_content

        sent = await send_email(
            html_content=html_to_send,
            subject=subject,
            recipients=effective_recipients,
        )

        if sent:
            if not test and not recipients:
                # Only mark as sent for real sends to the default recipient list
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
    parser.add_argument(
        "--rerender",
        action="store_true",
        default=False,
        help="Re-render email HTML from stored stories before sending.",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        default=False,
        help="Send only to TEST_EMAIL from .env. Newsletter is NOT marked as sent.",
    )
    args = parser.parse_args()

    asyncio.run(main(args.date, args.recipients or None, rerender=args.rerender, test=args.test))
