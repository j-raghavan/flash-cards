# SPDX-License-Identifier: AGPL-3.0-only
"""Study progress: undo snapshots on review logs, study settings, plan baselines

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
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
    # Each owner's most recent answer: the one undo may take back.
    op.create_table(
        "review_last_answers",
        sa.Column("owner_id", sa.String(), primary_key=True),
        sa.Column("review_id", sa.String(), nullable=False),
        sa.Column("card_id", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=True),
        sa.Column("undone", sa.Boolean(), nullable=False),
    )

    op.create_table(
        "study_settings",
        sa.Column("owner_id", sa.String(), primary_key=True),
        sa.Column("daily_goal", sa.Integer(), nullable=False),
        sa.Column("time_zone", sa.String(), nullable=False),
    )

    op.create_table(
        "plan_baselines",
        sa.Column(
            "goal_id",
            sa.String(),
            sa.ForeignKey("goals.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("taken_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exam_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("target_retention", sa.Float(), nullable=False),
        sa.Column("days_until_exam", sa.Integer(), nullable=False),
        sa.Column("total_cards", sa.Integer(), nullable=False),
        sa.Column("new_cards", sa.Integer(), nullable=False),
        sa.Column("scheduled_cards", sa.Integer(), nullable=False),
        sa.Column("new_per_day", sa.Integer(), nullable=False),
        sa.Column("reviews_per_day", sa.Integer(), nullable=False),
        sa.Column("readiness", sa.Float(), nullable=False),
        sa.Column("on_track", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_plan_baselines_owner_id", "plan_baselines", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_plan_baselines_owner_id", table_name="plan_baselines")
    op.drop_table("plan_baselines")
    op.drop_table("study_settings")
    op.drop_table("review_last_answers")
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
