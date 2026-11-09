# SPDX-License-Identifier: AGPL-3.0-only
"""Share link expiry and AI spend holds

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Links made before expiry existed keep working until revoked (null: never).
    op.add_column(
        "share_links", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    # Estimated costs held against the spend cap while AI calls run.
    op.create_table(
        "ai_spend_holds",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("period", sa.String(), nullable=False),
        sa.Column("amount_micro_usd", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ai_spend_holds_period", "ai_spend_holds", ["period"])


def downgrade() -> None:
    op.drop_index("ix_ai_spend_holds_period", table_name="ai_spend_holds")
    op.drop_table("ai_spend_holds")
    op.drop_column("share_links", "expires_at")
