"""
services/newsletter/renderer.py
---------------------------------
Render a Newsletter dataclass to HTML using the Jinja2 templates.

Two templates are maintained:
  - newsletter_web.html   : Full interactive version hosted on the server.
                            Includes the JS reading tracker, expand/collapse
                            details, and IntersectionObserver instrumentation.
  - newsletter_email.html : Email-safe version sent via SMTP. All content is
                            fully expanded; no JavaScript or interactive
                            elements are included.

Usage:
    from services.newsletter.renderer import render_web_html, render_email_html
    web_html   = render_web_html(newsletter)
    email_html = render_email_html(newsletter)

    # Convenience: render both in one call
    from services.newsletter.renderer import render_both
    render_both(newsletter)   # sets newsletter.html_content and newsletter.email_html_content
"""
import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from core.config import settings
from core.schemas.models import Newsletter
from services.newsletter._domains import infer_domain

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "templates"

# ── Custom Jinja2 filters ─────────────────────────────────────────────────────

def _slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"[\s_-]+", "-", text).strip("-")


# ── Jinja2 environment ────────────────────────────────────────────────────────

_env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=select_autoescape(["html"]),
)
_env.filters["domain_for"] = infer_domain
_env.filters["slugify"] = _slugify
_env.globals["zip"] = zip

_TEMPLATE_CONTEXT = lambda n: dict(date=n.date, stories=n.stories)  # noqa: E731


def render_web_html(newsletter: Newsletter) -> str:
    """Render the interactive web version (with JS reading tracker).

    Sets ``newsletter.html_content`` and returns the rendered string.
    """
    html = _env.get_template("newsletter_web.html").render(**_TEMPLATE_CONTEXT(newsletter))
    newsletter.html_content = html
    return html


def render_email_html(newsletter: Newsletter) -> str:
    """Render the email-safe version (no JS, all sections fully expanded).

    Sets ``newsletter.email_html_content`` and returns the rendered string.
    """
    html = _env.get_template("newsletter_email.html").render(**_TEMPLATE_CONTEXT(newsletter))
    web_url = f"{settings.public_url}/api/newsletter/{newsletter.date.isoformat()}"
    html = html.replace("__WEB_LINK__", web_url)
    newsletter.email_html_content = html
    return html


def render_both(newsletter: Newsletter) -> tuple[str, str]:
    """Render both the web and email versions in one call.

    Returns ``(web_html, email_html)`` and updates both fields on the
    newsletter object.
    """
    web_html = render_web_html(newsletter)
    email_html = render_email_html(newsletter)
    return web_html, email_html


# ── Backward-compatible alias ─────────────────────────────────────────────────

def render_html(newsletter: Newsletter) -> str:
    """Backward-compatible alias for ``render_web_html``."""
    return render_web_html(newsletter)
