"""
services/newsletter/assembler.py
----------------------------------
Assemble adapted KnowledgeStory objects into a Newsletter dataclass,
grouping stories into sections.

Usage:
    from services.newsletter.assembler import assemble
    newsletter = assemble(stories, target_date)
"""
import logging
from datetime import date
from typing import List

from core.schemas.models import KnowledgeStory, Newsletter
from services.newsletter._domains import infer_domain

log = logging.getLogger(__name__)


def assemble(stories: List[KnowledgeStory], target_date: date) -> Newsletter:
    """
    Assemble adapted stories into a Newsletter.
    Domain grouping for the template is handled by renderer.py via the
    shared `infer_domain` helper — no need to duplicate it here.
    html_content is populated later by renderer.py.
    """
    # Count unique domains for the log line
    domains = {infer_domain(s.topic_label) for s in stories}
    log.info(
        "Assembled newsletter for %s: %d stories across %d sections",
        target_date,
        len(stories),
        len(domains),
    )

    return Newsletter(
        date=target_date,
        stories=stories,
    )
