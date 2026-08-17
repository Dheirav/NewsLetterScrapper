"""Add semantic duplicate marking and an HNSW index on article embeddings

Revision ID: 008_semantic_dedup
Revises: 007_article_source_metadata
Create Date: 2026-08-17

Why
---
Deduplication only compared exact URLs against the database plus TF-IDF title
similarity *within the current batch*. A story republished the next day under a
new URL — wire copy, a retitled feature, a syndicated post — passed both stages,
was embedded again, and could form a second cluster covering an event that had
already been briefed.

Changes
-------
1. ``articles.duplicate_of`` — self-referencing FK to the article this one
   duplicates. Duplicates are MARKED rather than deleted: the row must stay so
   the URL dedup stage keeps recognising it. Deleting instead would let the
   same URL be re-fetched, re-scraped and re-embedded on every subsequent run.

2. An HNSW index on ``articles.embedding`` using cosine distance. Without it
   the nearest-neighbour lookup is a sequential scan over every stored vector;
   measured on 13,836 rows that does not complete in a workable time.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "008_semantic_dedup"
down_revision: Union[str, None] = "007_article_source_metadata"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "articles",
        sa.Column("duplicate_of", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_articles_duplicate_of",
        "articles", "articles",
        ["duplicate_of"], ["id"],
        ondelete="SET NULL",
    )
    # Partial: only a small minority of rows are ever marked.
    op.create_index(
        "ix_articles_duplicate_of",
        "articles",
        ["duplicate_of"],
        postgresql_where=sa.text("duplicate_of IS NOT NULL"),
    )

    # Cosine distance (<=>) matches how the pipeline compares embeddings.
    # m/ef_construction are pgvector's defaults, ample for this table size.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_articles_embedding_hnsw "
        "ON articles USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_articles_embedding_hnsw")
    op.drop_index("ix_articles_duplicate_of", table_name="articles")
    op.drop_constraint("fk_articles_duplicate_of", "articles", type_="foreignkey")
    op.drop_column("articles", "duplicate_of")
