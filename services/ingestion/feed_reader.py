"""
services/ingestion/feed_reader.py
----------------------------------
Fetch raw articles from all configured RSS feeds.

Usage:
    from services.ingestion.feed_reader import fetch_all_feeds
    articles = await fetch_all_feeds()
"""
import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List

import feedparser
import yaml

from core.config import settings
from core.schemas.models import Article

log = logging.getLogger(__name__)

SOURCES_PATH = Path(__file__).parent / "sources.yaml"


def _load_sources(path: Path = SOURCES_PATH) -> list[dict]:
    with open(path) as f:
        data = yaml.safe_load(f)
    return data.get("sources", [])


def _parse_date(entry) -> datetime:
    """Return a timezone-aware datetime from a feedparser entry."""
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
    return datetime.now(tz=timezone.utc)


def _fetch_feed(source: dict) -> List[Article]:
    """Synchronous fetch of a single RSS feed (feedparser is sync)."""
    articles: List[Article] = []
    try:
        feed = feedparser.parse(source["url"])
        for entry in feed.entries[:settings.feed_max_per_source]:
            url = entry.get("link", "")
            title = entry.get("title", "").strip()
            if not url or not title:
                continue
            # Use the RSS summary as initial content; scraper.py will enrich it
            # Guard: entry["content"] may exist but be an empty list (feedparser quirk)
            content = entry.get("summary", "") or (entry.get("content") or [{}])[0].get("value", "")
            articles.append(
                Article(
                    title=title,
                    source=source["name"],
                    url=url,
                    published_at=_parse_date(entry),
                    content=content,
                    source_type=source.get("source_type", "news"),
                    source_weight=float(source.get("weight", 1.0)),
                )
            )
        log.debug("Feed %s → %d articles", source["name"], len(articles))
    except Exception as exc:
        log.warning("Failed to fetch feed %s: %s", source.get("name"), exc)
    return articles


async def fetch_all_feeds(sources_path: Path = SOURCES_PATH) -> List[Article]:
    """
    Fetch all RSS feeds concurrently (using asyncio thread-pool for sync feedparser).
    Returns a flat list of Article objects — content is RSS summary only at this stage.
    """
    sources = _load_sources(sources_path)
    log.info("Fetching %d RSS feeds …", len(sources))

    loop = asyncio.get_running_loop()
    tasks = [
        loop.run_in_executor(None, _fetch_feed, source)
        for source in sources
    ]
    results = await asyncio.gather(*tasks)

    articles: List[Article] = []
    for batch in results:
        articles.extend(batch)

    log.info("Collected %d raw articles from feeds", len(articles))
    return articles
