"""Convert JSON Text columns to JSONB and add missing index

Revision ID: 003_jsonb_and_indexes
Revises: 002_add_article_sources
Create Date: 2026-02-22

Changes
-------
1. knowledge_stories.talking_points   TEXT → JSONB
2. knowledge_stories.article_urls     TEXT → JSONB
3. knowledge_stories.article_sources  TEXT → JSONB
4. reading_events.sections_read       TEXT → JSONB
5. user_profiles.topic_weights        TEXT → JSONB
6. user_profiles.source_affinity      TEXT → JSONB
7. story_clusters: add ix_story_clusters_date index

The USING cast (::jsonb) is safe because all existing rows contain
valid JSON — the application has always written well-formed JSON text.
If you are recreating the DB from scratch this migration is still safe
(it will run on empty tables).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003_jsonb_and_indexes"
down_revision: Union[str, None] = "002_add_article_sources"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Helper: drop server default → cast to JSONB → set new JSONB default
    def _to_jsonb(table: str, column: str, empty_value: str) -> None:
        conn.execute(sa.text(
            f"ALTER TABLE {table} ALTER COLUMN {column} DROP DEFAULT"
        ))
        conn.execute(sa.text(
            f"ALTER TABLE {table} ALTER COLUMN {column} "
            f"TYPE JSONB USING {column}::jsonb"
        ))
        conn.execute(sa.text(
            f"ALTER TABLE {table} ALTER COLUMN {column} "
            f"SET DEFAULT '{empty_value}'::jsonb"
        ))

    # ── knowledge_stories ─────────────────────────────────────────────────────
    _to_jsonb("knowledge_stories", "talking_points",  "[]")
    _to_jsonb("knowledge_stories", "article_urls",    "[]")
    _to_jsonb("knowledge_stories", "article_sources", "[]")

    # ── reading_events ────────────────────────────────────────────────────────
    _to_jsonb("reading_events", "sections_read", "[]")

    # ── user_profiles ─────────────────────────────────────────────────────────
    _to_jsonb("user_profiles", "topic_weights",   "{}")
    _to_jsonb("user_profiles", "source_affinity", "{}")

    # ── story_clusters — missing date index ───────────────────────────────────
    op.create_index("ix_story_clusters_date", "story_clusters", ["cluster_date"])


def downgrade() -> None:
    conn = op.get_bind()
    op.drop_index("ix_story_clusters_date", table_name="story_clusters")

    def _to_text(table: str, column: str, empty_value: str) -> None:
        conn.execute(sa.text(
            f"ALTER TABLE {table} ALTER COLUMN {column} DROP DEFAULT"
        ))
        conn.execute(sa.text(
            f"ALTER TABLE {table} ALTER COLUMN {column} TYPE TEXT USING {column}::text"
        ))
        conn.execute(sa.text(
            f"ALTER TABLE {table} ALTER COLUMN {column} SET DEFAULT '{empty_value}'"
        ))

    _to_text("user_profiles", "source_affinity", "{}")
    _to_text("user_profiles", "topic_weights",   "{}")
    _to_text("reading_events", "sections_read",  "[]")
    _to_text("knowledge_stories", "article_sources", "[]")
    _to_text("knowledge_stories", "article_urls",    "[]")
    _to_text("knowledge_stories", "talking_points",  "[]")
