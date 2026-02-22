"""
PROJECT CONTEXT:

This project is a daily intelligence newsletter system.

Pipeline:
1. News ingestion
2. Embedding generation
3. Story clustering
4. Knowledge generation
5. Newsletter assembly
6. Personalization from user reading behavior

This file defines core shared data models used across all modules.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------

@dataclass
class Article:
    """A single news article fetched from an RSS feed or scraped URL."""
    title: str
    source: str          # human-readable source name, e.g. "Reuters"
    url: str
    published_at: datetime
    content: str
    id: Optional[int] = None                  # set after DB persistence
    cluster_id: Optional[str] = None          # set after clustering
    embedding: Optional[List[float]] = None   # 768-dim vector (nomic-embed-text)
    # 'ok' = full text scraped; 'low' = paywalled / JS-rendered / near-empty
    content_quality: str = "ok"


# ---------------------------------------------------------------------------
# Understanding
# ---------------------------------------------------------------------------

@dataclass
class StoryCluster:
    """Multiple articles covering the same real-world event."""
    cluster_id: str       # UUID string
    topic_label: str      # short LLM-generated label, e.g. "US-China Trade Tariffs"
    articles: List[Article]
    created_at: datetime


# ---------------------------------------------------------------------------
# Knowledge
# ---------------------------------------------------------------------------

@dataclass
class KnowledgeStory:
    """Structured deep understanding generated from a StoryCluster."""
    cluster_id: str
    topic_label: str
    executive_summary: str    # 2-3 sentence quick read
    context: str              # background, history, prior events
    why_it_matters: str       # impact on politics / economy / technology / society
    implications: str         # what might happen next and why people care
    talking_points: List[str] # 4-6 conversation-ready bullets
    reliability_notes: Optional[str] = None   # prose explanation of source quality
    source_count: int = 0
    article_urls: List[str] = field(default_factory=list)
    article_sources: List[str] = field(default_factory=list)  # source name per URL (same order)


# ---------------------------------------------------------------------------
# Newsletter
# ---------------------------------------------------------------------------

@dataclass
class Newsletter:
    """The daily assembled intelligence briefing."""
    date: date
    stories: List[KnowledgeStory]
    html_content: str = ""
    id: Optional[int] = None
    sent_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Reading behaviour
# ---------------------------------------------------------------------------

@dataclass
class ReadingEvent:
    """
    A single reading session recorded by the web tracker.
    Captured client-side (IntersectionObserver + beforeunload) and POSTed to
    POST /events/reading.
    """
    date: date
    topic_slug: str           # slugified topic_label, used as the join key
    time_spent_seconds: int
    scroll_percent: float     # 0.0 – 100.0
    sections_read: List[str]  # e.g. ["executive_summary", "implications"]
    id: Optional[int] = None
    created_at: Optional[datetime] = None


@dataclass
class UserProfile:
    """
    Aggregated reading preferences inferred from ReadingEvents.
    Rebuilt nightly from the last N days of events.
    """
    # topic slug → normalised weight (higher = read more deeply)
    topic_weights: Dict[str, float] = field(default_factory=dict)
    # source name → engagement score (avg time spent on articles from that source)
    source_affinity: Dict[str, float] = field(default_factory=dict)
    # average scroll depth across all events (0-100)
    preferred_depth: float = 50.0
    updated_at: Optional[datetime] = None
    id: Optional[int] = None
