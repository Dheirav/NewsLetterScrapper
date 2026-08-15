"""Add email_html_content column to newsletters

Revision ID: 005_newsletter_email_html
Revises: 004_reliability_features
Create Date: 2026-03-01

Changes
-------
1. newsletters: add email_html_content TEXT column (nullable)
   Stores the email-safe rendering (no JS, all sections fully expanded)
   separately from html_content which is the interactive web version.
   Nullable so that existing rows continue to work — send_newsletter.py
   falls back to html_content when email_html_content is NULL.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "005_newsletter_email_html"
down_revision: Union[str, None] = "004_reliability_features"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "newsletters",
        sa.Column("email_html_content", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("newsletters", "email_html_content")
