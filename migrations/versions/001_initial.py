"""Initial schema — all tables

Revision ID: 001_initial
Revises:
Create Date: 2026-02-21 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # story_clusters must be created before articles (FK target)
    op.create_table(
        "story_clusters",
        sa.Column("cluster_id", sa.String(36), primary_key=True),
        sa.Column("topic_label", sa.Text, nullable=False),
        sa.Column("cluster_date", sa.Date, nullable=False),
        sa.Column("article_count", sa.Integer, default=0),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "articles",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("source", sa.String(255), nullable=False),
        sa.Column("url", sa.Text, nullable=False, unique=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("embedding", Vector(768), nullable=True),
        sa.Column(
            "cluster_id",
            sa.String(36),
            sa.ForeignKey("story_clusters.cluster_id"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_articles_url", "articles", ["url"], unique=True)
    op.create_index("ix_articles_cluster_id", "articles", ["cluster_id"])

    op.create_table(
        "knowledge_stories",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "cluster_id",
            sa.String(36),
            sa.ForeignKey("story_clusters.cluster_id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("topic_label", sa.Text, nullable=False),
        sa.Column("executive_summary", sa.Text, nullable=False),
        sa.Column("context", sa.Text, nullable=False),
        sa.Column("why_it_matters", sa.Text, nullable=False),
        sa.Column("implications", sa.Text, nullable=False),
        sa.Column("talking_points", sa.Text, nullable=False, server_default="[]"),
        sa.Column("reliability_notes", sa.Text, nullable=True),
        sa.Column("source_count", sa.Integer, default=0),
        sa.Column("article_urls", sa.Text, nullable=False, server_default="[]"),
        sa.Column("story_date", sa.Date, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_knowledge_stories_story_date", "knowledge_stories", ["story_date"])

    op.create_table(
        "newsletters",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("newsletter_date", sa.Date, nullable=False, unique=True),
        sa.Column("html_content", sa.Text, nullable=False),
        sa.Column("sent", sa.Boolean, default=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "reading_events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("event_date", sa.Date, nullable=False),
        sa.Column("topic_slug", sa.String(255), nullable=False),
        sa.Column("time_spent_seconds", sa.Integer, nullable=False),
        sa.Column("scroll_percent", sa.Float, nullable=False),
        sa.Column("sections_read", sa.Text, nullable=False, server_default="[]"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_reading_events_event_date", "reading_events", ["event_date"])
    op.create_index("ix_reading_events_topic_slug", "reading_events", ["topic_slug"])

    op.create_table(
        "user_profiles",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("topic_weights", sa.Text, nullable=False, server_default="{}"),
        sa.Column("source_affinity", sa.Text, nullable=False, server_default="{}"),
        sa.Column("preferred_depth", sa.Float, default=50.0),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("user_profiles")
    op.drop_table("reading_events")
    op.drop_table("newsletters")
    op.drop_table("knowledge_stories")
    op.drop_table("articles")
    op.drop_table("story_clusters")
