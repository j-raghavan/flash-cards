# SPDX-License-Identifier: AGPL-3.0-only
"""M5 mock-exam schema: mock_exams

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mock_exams",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("deck_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("time_limit_seconds", sa.Integer(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("questions", sa.JSON(), nullable=False),
    )
    op.create_index("ix_mock_exams_owner_id", "mock_exams", ["owner_id"])
    op.create_index("ix_mock_exams_deck_id", "mock_exams", ["deck_id"])


def downgrade() -> None:
    op.drop_table("mock_exams")
