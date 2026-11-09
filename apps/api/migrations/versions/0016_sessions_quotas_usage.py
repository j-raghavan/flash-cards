# SPDX-License-Identifier: AGPL-3.0-only
"""Session devices and activity, AI quotas and usage records

Revision ID: 0016
Revises: 0014
Create Date: 2026-11-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column("device", sa.String(), nullable=False, server_default="Unknown device"),
    )
    op.create_table(
        "session_activity",
        sa.Column(
            "session_id",
            sa.String(),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
    )
    # Sessions that exist already were last seen, as far as anyone knows, at sign-in.
    op.execute(
        "INSERT INTO session_activity (session_id, last_seen_at) "
        "SELECT id, created_at FROM sessions"
    )
    op.create_table(
        "ai_usage",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("feature", sa.String(), nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False),
        sa.Column("cost_micro_usd", sa.BigInteger(), nullable=False),
        sa.Column("period", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ai_usage_owner_id", "ai_usage", ["owner_id"])
    op.create_index("ix_ai_usage_period", "ai_usage", ["period"])
    op.create_table(
        "instance_quotas",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("default_monthly_pages", sa.Integer(), nullable=False),
        sa.Column("monthly_spend_cap_micro_usd", sa.BigInteger(), nullable=True),
    )
    op.create_table(
        "user_quotas",
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("monthly_pages", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("user_quotas")
    op.drop_table("instance_quotas")
    op.drop_index("ix_ai_usage_period", table_name="ai_usage")
    op.drop_index("ix_ai_usage_owner_id", table_name="ai_usage")
    op.drop_table("ai_usage")
    op.drop_table("session_activity")
    op.drop_column("sessions", "device")
