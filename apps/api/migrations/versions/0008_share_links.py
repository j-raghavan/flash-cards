# SPDX-License-Identifier: AGPL-3.0-only
"""Deck sharing schema: share_links

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "share_links",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("deck_id", sa.String(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("allow_clone", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_share_links_owner_id", "share_links", ["owner_id"])
    op.create_index("ix_share_links_deck_id", "share_links", ["deck_id"])
    op.create_index("ix_share_links_token_hash", "share_links", ["token_hash"], unique=True)


def downgrade() -> None:
    op.drop_table("share_links")
