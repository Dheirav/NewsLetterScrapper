"""
services/ingestion/scraper.py
------------------------------
Enrich Article objects with full article text extracted from each URL.

Uses trafilatura for reliable content extraction.
Articles whose content cannot be fetched are kept with their RSS summary.

Usage:
    from services.ingestion.scraper import enrich_content
    articles = await enrich_content(articles)
"""
import asyncio
import logging
from typing import List

import httpx
import trafilatura

from core.schemas.models import Article

log = logging.getLogger(__name__)

from core.config import settings

# Request timeout in seconds
REQUEST_TIMEOUT = 15


async def _fetch_url(client: httpx.AsyncClient, url: str) -> str | None:
    """Fetch raw HTML for a URL. Returns None on error."""
    try:
        response = await client.get(url)
        response.raise_for_status()
        return response.text
    except Exception as exc:
        log.debug("HTTP error for %s: %s", url, exc)
        return None


def _extract_text(html: str) -> str | None:
    """Extract main article text from raw HTML using trafilatura."""
    return trafilatura.extract(
        html,
        include_comments=False,
        include_tables=False,
        no_fallback=False,
    )


async def enrich_content(articles: List[Article]) -> List[Article]:
    """
    For each article, attempt to scrape the full article text from its URL.
    If scraping fails or returns too little text, the RSS summary is kept.
    Returns the same list with .content fields updated in-place.

    trafilatura.extract() is CPU-bound so it is offloaded to a thread pool
    via run_in_executor to avoid blocking the async event loop.
    """
    log.info("Enriching content for %d articles …", len(articles))

    semaphore = asyncio.Semaphore(settings.scrape_concurrency)
    loop = asyncio.get_running_loop()

    async with httpx.AsyncClient(
        headers={"User-Agent": "Mozilla/5.0 (compatible; IntelligenceBot/1.0)"},
        follow_redirects=True,
        timeout=REQUEST_TIMEOUT,
    ) as client:

        async def enrich_one(article: Article) -> None:
            try:
                async with semaphore:
                    html = await _fetch_url(client, article.url)
                    if html:
                        # CPU-bound — run in thread pool to avoid blocking the loop
                        extracted = await loop.run_in_executor(None, _extract_text, html)
                        if extracted and len(extracted) >= settings.min_content_length:
                            article.content = extracted
                            article.content_quality = "ok"
                            return
                # Below threshold or fetch failed — keep RSS summary, flag quality
                if len(article.content) < settings.min_content_length:
                    article.content_quality = "low"
                    log.debug(
                        "content_quality=low for %s (content=%d chars)",
                        article.url,
                        len(article.content),
                    )
                else:
                    log.debug("Kept RSS summary for: %s", article.url)
            except Exception as exc:
                log.warning("enrich_one failed for %s: %s", article.url, exc)

        await asyncio.gather(*[enrich_one(a) for a in articles])

    log.info("Content enrichment complete")
    return articles
