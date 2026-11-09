# SPDX-License-Identifier: AGPL-3.0-only
"""Study progress: study settings and a review-log index

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
