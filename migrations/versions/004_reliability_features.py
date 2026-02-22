"""Add content_quality, pipeline_runs, and failed_generations

Revision ID: 004_reliability_features
Revises: 003_jsonb_and_indexes
Create Date: 2026-02-22

Changes
-------
1. articles: add content_quality TEXT column (default 'ok')
2. Create pipeline_runs — per-step completion log for crash recovery
3. Create failed_generations — dead-letter queue for LLM generation failures
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004_reliability_features"
down_revision: Union[str, None] = "003_jsonb_and_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. articles: content_quality column ──────────────────────────────────
    op.add_column(
        "articles",
        sa.Column(
            "content_quality",
            sa.String(10),
            nullable=False,
            server_default="ok",
        ),
    )

    # ── 2. pipeline_runs ─────────────────────────────────────────────────────
    op.create_table(
        "pipeline_runs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("run_date", sa.Date, nullable=False),
        sa.Column("step_name", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="completed"),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_pipeline_runs_date_step",
        "pipeline_runs",
        ["run_date", "step_name"],
        unique=True,
    )

    # ── 3. failed_generations ─────────────────────────────────────────────────
    op.create_table(
        "failed_generations",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("cluster_id", sa.String(36), nullable=False),
        sa.Column("topic_label", sa.Text, nullable=False),
        sa.Column("error_message", sa.Text, nullable=False),
        sa.Column("mode", sa.String(16), nullable=False, server_default="concise"),
        sa.Column("run_date", sa.Date, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_failed_generations_run_date",
        "failed_generations",
        ["run_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_failed_generations_run_date", table_name="failed_generations")
    op.drop_table("failed_generations")

    op.drop_index("ix_pipeline_runs_date_step", table_name="pipeline_runs")
    op.drop_table("pipeline_runs")

    op.drop_column("articles", "content_quality")
