"""
services/understanding/embedder.py
------------------------------------
Generate semantic embeddings for articles using a local Ollama model
(default: nomic-embed-text, which produces 768-dimensional vectors).

Usage:
    from services.understanding.embedder import embed_articles
    articles = await embed_articles(articles, session)
"""
import asyncio
import logging
from typing import List

import ollama
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from core.config import settings
from core.db.orm_models import ArticleORM
from core.schemas.models import Article

log = logging.getLogger(__name__)


def _build_embed_input(article: Article) -> str:
    """Combine title + first 1 000 chars of content for embedding."""
    body = article.content[:1000] if article.content else ""
    return f"{article.title}\n\n{body}"


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((ConnectionError, TimeoutError, OSError)),
    reraise=True,
)
def _embed_sync(text: str) -> List[float]:
    """Synchronous Ollama embedding call (run in thread pool) with auto-retry."""
    response = ollama.embeddings(model=settings.ollama_embed_model, prompt=text)
    return response["embedding"]


async def embed_articles(
    articles: List[Article],
    session: AsyncSession,
) -> List[Article]:
    """
    For each article:
      1. Call the local Ollama embedding model
      2. Set article.embedding
      3. Persist the vector to ArticleORM.embedding
    Returns articles with .embedding populated.
    """
    log.info("Generating embeddings for %d articles …", len(articles))
    loop = asyncio.get_running_loop()

    async def compute_embedding(article: Article):
        """Compute embedding via thread pool — no DB access here."""
        try:
            text = _build_embed_input(article)
            embedding = await loop.run_in_executor(None, _embed_sync, text)
            return article, embedding
        except Exception as exc:
            log.warning("Embedding failed for '%s': %s", article.title, exc)
            return article, None

    # Gather embeddings concurrently, then write to DB sequentially.
    # AsyncSession is not safe for concurrent coroutine access,
    # so all session operations are outside the asyncio.gather call.
    for i in range(0, len(articles), settings.embed_batch_size):
        batch = articles[i : i + settings.embed_batch_size]
        pairs = await asyncio.gather(*[compute_embedding(a) for a in batch])
        for article, embedding in pairs:
            if embedding is None:
                continue
            article.embedding = embedding
            if article.id:
                # Direct UPDATE — avoids a round-trip SELECT before writing
                await session.execute(
                    sa_update(ArticleORM)
                    .where(ArticleORM.id == article.id)
                    .values(embedding=embedding)
                )
        log.debug("Embedded batch %d/%d", min(i + settings.embed_batch_size, len(articles)), len(articles))

    embedded = [a for a in articles if a.embedding is not None]
    log.info("Embeddings generated: %d/%d articles", len(embedded), len(articles))
    return articles
