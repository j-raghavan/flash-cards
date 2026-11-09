# SPDX-License-Identifier: AGPL-3.0-only
"""Study progress: undo snapshots on review logs, study settings

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The schedule a card had just before each review, so the answer can be undone.
    op.add_column("review_logs", sa.Column("undo_kind", sa.String(), nullable=True))
    op.add_column("review_logs", sa.Column("prior_state", sa.String(), nullable=True))
    op.add_column("review_logs", sa.Column("prior_step", sa.Integer(), nullable=True))
    op.add_column("review_logs", sa.Column("prior_stability", sa.Float(), nullable=True))
    op.add_column("review_logs", sa.Column("prior_difficulty", sa.Float(), nullable=True))
    op.add_column(
        "review_logs", sa.Column("prior_due_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "review_logs",
        sa.Column("prior_last_reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_review_logs_owner_reviewed", "review_logs", ["owner_id", "reviewed_at"]
    )

    op.create_table(
        "study_settings",
        sa.Column("owner_id", sa.String(), primary_key=True),
        sa.Column("daily_goal", sa.Integer(), nullable=False),
        sa.Column("time_zone", sa.String(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("study_settings")
    op.drop_index("ix_review_logs_owner_reviewed", table_name="review_logs")
    for column in (
        "prior_last_reviewed_at",
        "prior_due_at",
        "prior_difficulty",
        "prior_stability",
        "prior_step",
        "prior_state",
        "undo_kind",
    ):
        op.drop_column("review_logs", column)
