"""
SQLAlchemy ORM table definitions for the entire system.

Tables
------
articles            — raw ingested articles with pgvector embedding column
story_clusters      — groups of articles covering the same event
knowledge_stories   — structured deep-knowledge pieces per cluster
newsletters         — assembled daily HTML briefings
reading_events      — individual user reading sessions tracked client-side
user_profiles       — aggregated reading preferences (one row per user)
pipeline_runs       — per-step completion log for crash recovery
failed_generations  — dead-letter queue for LLM generation failures
"""
from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    false,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from pgvector.sqlalchemy import Vector

from core.db.base import Base
from core.config import settings


# ---------------------------------------------------------------------------
# Articles
# ---------------------------------------------------------------------------

class ArticleORM(Base):
    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # 'ok' = full text scraped; 'low' = below MIN_CONTENT_LENGTH (paywall / JS)
    content_quality: Mapped[str] = mapped_column(String(10), nullable=False, server_default="ok")
    source_type: Mapped[str] = mapped_column(String(16), nullable=False, server_default="news")
    source_weight: Mapped[float] = mapped_column(Float, nullable=False, server_default="1.0")
    embedding: Mapped[Optional[list]] = mapped_column(
        Vector(settings.embedding_dim), nullable=True
    )
    cluster_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("story_clusters.cluster_id"), nullable=True
    )
    # Set when this article is a near-identical restatement of an earlier one
    # (republished under a new URL, retitled, or syndicated). Marked rather than
    # deleted so URL dedup keeps recognising it — deleting would let the same
    # URL be re-fetched and re-embedded on every subsequent run.
    duplicate_of: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("articles.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    cluster: Mapped[Optional["StoryClusterORM"]] = relationship(
        back_populates="articles"
    )


# ---------------------------------------------------------------------------
# Story clusters
# ---------------------------------------------------------------------------

class StoryClusterORM(Base):
    __tablename__ = "story_clusters"
    __table_args__ = (
        Index("ix_story_clusters_date", "cluster_date"),
    )

    cluster_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    topic_label: Mapped[str] = mapped_column(Text, nullable=False)
    cluster_date: Mapped[date] = mapped_column(Date, nullable=False)
    article_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    articles: Mapped[list["ArticleORM"]] = relationship(back_populates="cluster")
    knowledge_story: Mapped[Optional["KnowledgeStoryORM"]] = relationship(
        back_populates="cluster", uselist=False
    )


# ---------------------------------------------------------------------------
# Knowledge stories
# ---------------------------------------------------------------------------

class KnowledgeStoryORM(Base):
    __tablename__ = "knowledge_stories"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    cluster_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("story_clusters.cluster_id"), nullable=False, unique=True
    )
    topic_label: Mapped[str] = mapped_column(Text, nullable=False)
    executive_summary: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[str] = mapped_column(Text, nullable=False)
    why_it_matters: Mapped[str] = mapped_column(Text, nullable=False)
    implications: Mapped[str] = mapped_column(Text, nullable=False)
    # JSONB arrays — SQLAlchemy serializes/deserializes automatically
    talking_points: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    reliability_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_count: Mapped[int] = mapped_column(Integer, default=0)
    article_urls: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    article_sources: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    story_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    cluster: Mapped["StoryClusterORM"] = relationship(back_populates="knowledge_story")


# ---------------------------------------------------------------------------
# Newsletters
# ---------------------------------------------------------------------------

class NewsletterORM(Base):
    __tablename__ = "newsletters"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    newsletter_date: Mapped[date] = mapped_column(Date, nullable=False, unique=True)
    html_content: Mapped[str] = mapped_column(Text, nullable=False)                         # web version
    email_html_content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)          # email version
    sent: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    sent_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ---------------------------------------------------------------------------
# Reading events
# ---------------------------------------------------------------------------

class ReadingEventORM(Base):
    __tablename__ = "reading_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    topic_slug: Mapped[str] = mapped_column(String(255), nullable=False)
    time_spent_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    scroll_percent: Mapped[float] = mapped_column(Float, nullable=False)
    # JSONB array of section names, e.g. ["executive_summary", "implications"]
    sections_read: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ---------------------------------------------------------------------------
# User profile  (single-row table — always upsert id=1)
# ---------------------------------------------------------------------------

class UserProfileORM(Base):
    __tablename__ = "user_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    # JSONB dicts — SQLAlchemy serializes/deserializes automatically
    topic_weights: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    source_affinity: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    preferred_depth: Mapped[float] = mapped_column(Float, default=50.0)
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=True
    )


# ---------------------------------------------------------------------------
# Pipeline run checkpoints
# ---------------------------------------------------------------------------

class PipelineRunORM(Base):
    """
    Records which pipeline steps completed for a given run date.
    On a crash/restart the pipeline checks this table and skips steps
    that already have status='completed', avoiding expensive LLM re-runs.
    """
    __tablename__ = "pipeline_runs"
    __table_args__ = (
        Index("ix_pipeline_runs_date_step", "run_date", "step_name", unique=True),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_date: Mapped[date] = mapped_column(Date, nullable=False)
    step_name: Mapped[str] = mapped_column(String(64), nullable=False)
    # 'completed' | 'failed'
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="completed")
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ---------------------------------------------------------------------------
# Failed knowledge-generation dead-letter queue
# ---------------------------------------------------------------------------

class FailedGenerationORM(Base):
    """
    Persists cluster IDs whose LLM generation failed after all retries.
    Lets you inspect and manually retry them without rerunning the full pipeline.
    """
    __tablename__ = "failed_generations"
    __table_args__ = (
        Index("ix_failed_generations_run_date", "run_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    cluster_id: Mapped[str] = mapped_column(String(36), nullable=False)
    topic_label: Mapped[str] = mapped_column(Text, nullable=False)
    error_message: Mapped[str] = mapped_column(Text, nullable=False)
    # 'concise' | 'detailed'
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="concise")
    run_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ---------------------------------------------------------------------------
# Unsubscribes  (email opt-outs)
# ---------------------------------------------------------------------------

class UnsubscribeORM(Base):
    """One row per opted-out email address."""
    __tablename__ = "unsubscribes"
    __table_args__ = (
        Index("ix_unsubscribes_email", "email", unique=True),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    opted_out_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
