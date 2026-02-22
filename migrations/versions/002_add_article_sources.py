"""add article_sources column to knowledge_stories

Revision ID: 002
Revises: 001
Create Date: 2026-02-21
"""
from alembic import op
import sqlalchemy as sa

revision = "002_add_article_sources"
down_revision = "001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "knowledge_stories",
        sa.Column("article_sources", sa.Text(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("knowledge_stories", "article_sources")
