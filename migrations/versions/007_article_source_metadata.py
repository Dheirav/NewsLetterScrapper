"""Add source_type and source_weight columns to articles table

Revision ID: 007_article_source_metadata
Revises: 006_unsubscribe
Create Date: 2026-03-01

Changes
-------
1. Add `source_type` (varchar 16, NOT NULL, default 'news') to articles
2. Add `source_weight` (float, NOT NULL, default 1.0) to articles
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "007_article_source_metadata"
down_revision: Union[str, None] = "006_unsubscribe"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "articles",
        sa.Column(
            "source_type",
            sa.String(16),
            nullable=False,
            server_default="news",
        ),
    )
    op.add_column(
        "articles",
        sa.Column(
            "source_weight",
            sa.Float,
            nullable=False,
            server_default="1.0",
        ),
    )


def downgrade() -> None:
    op.drop_column("articles", "source_weight")
    op.drop_column("articles", "source_type")
