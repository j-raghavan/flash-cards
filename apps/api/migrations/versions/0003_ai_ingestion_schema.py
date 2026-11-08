# SPDX-License-Identifier: AGPL-3.0-only
"""M2 AI ingestion schema: ai_jobs, draft_cards, ai_blobs, ai_quota

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_jobs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("source_id", sa.String(), nullable=False),
        sa.Column("draft_deck_id", sa.String(), nullable=False),
        sa.Column("thoroughness", sa.String(), nullable=False),
        sa.Column("total_units", sa.Integer(), nullable=False),
        sa.Column("completed_units", sa.Integer(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ai_jobs_owner_id", "ai_jobs", ["owner_id"])
    op.create_index("ix_ai_jobs_status", "ai_jobs", ["status"])

    op.create_table(
        "draft_cards",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "job_id",
            sa.String(),
            sa.ForeignKey("ai_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("note_type", sa.String(), nullable=False),
        sa.Column("fields", sa.JSON(), nullable=False),
        sa.Column("source_page", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_draft_cards_job_id", "draft_cards", ["job_id"])
    op.create_index("ix_draft_cards_owner_id", "draft_cards", ["owner_id"])

    op.create_table(
        "ai_blobs",
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("content_type", sa.String(), nullable=False),
    )

    op.create_table(
        "ai_quota",
        sa.Column("owner_id", sa.String(), primary_key=True),
        sa.Column("period", sa.String(), primary_key=True),
        sa.Column("used_pages", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("ai_quota")
    op.drop_table("ai_blobs")
    op.drop_table("draft_cards")
    op.drop_table("ai_jobs")
