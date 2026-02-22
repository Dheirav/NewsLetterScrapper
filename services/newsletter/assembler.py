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
from services.newsletter._domains import infer_domain, SECTION_ORDER

log = logging.getLogger(__name__)

# Map each domain name to its display order index for fast lookup
_DOMAIN_RANK = {domain: i for i, domain in enumerate(SECTION_ORDER)}


def assemble(stories: List[KnowledgeStory], target_date: date) -> Newsletter:
    """
    Assemble adapted stories into a Newsletter.
    Stories are grouped by domain so all World stories appear together,
    all Technology stories together, etc., following SECTION_ORDER.
    Within each domain, the adapter's engagement ranking is preserved.
    """
    # Stable sort: primary key = domain order, secondary key = original position
    # (preserves adapter engagement ranking within each section)
    sorted_stories = sorted(
        stories,
        key=lambda s: _DOMAIN_RANK.get(infer_domain(s.topic_label), len(SECTION_ORDER)),
    )

    domains = {infer_domain(s.topic_label) for s in sorted_stories}
    log.info(
        "Assembled newsletter for %s: %d stories across %d sections",
        target_date,
        len(sorted_stories),
        len(domains),
    )

    return Newsletter(
        date=target_date,
        stories=sorted_stories,
    )

