"""Add unsubscribes table for email opt-outs

Revision ID: 006_unsubscribe
Revises: 005_newsletter_email_html
Create Date: 2026-03-01

Changes
-------
1. Create `unsubscribes` table:
   - id (bigserial PK)
   - email (varchar 255, unique, not null)
   - opted_out_at (timestamptz, server default now(), not null)
   - unique index ix_unsubscribes_email on email
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "006_unsubscribe"
down_revision: Union[str, None] = "005_newsletter_email_html"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "unsubscribes",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column(
            "opted_out_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_unsubscribes_email", "unsubscribes", ["email"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_unsubscribes_email", table_name="unsubscribes")
    op.drop_table("unsubscribes")
