"""
services/newsletter/renderer.py
---------------------------------
Render a Newsletter dataclass to HTML using the Jinja2 template.

Usage:
    from services.newsletter.renderer import render_html
    html = render_html(newsletter)
"""
import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

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


def render_html(newsletter: Newsletter) -> str:
    """Render the newsletter to an HTML string."""
    template = _env.get_template("newsletter.html")
    html = template.render(
        date=newsletter.date,
        stories=newsletter.stories,
    )
    newsletter.html_content = html
    return html
