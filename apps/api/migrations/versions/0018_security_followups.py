# SPDX-License-Identifier: AGPL-3.0-only
"""Share link expiry

Revision ID: 0018
Revises: 0016
Create Date: 2026-11-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Links made before expiry existed keep working until revoked (null: never).
    op.add_column(
        "share_links", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("share_links", "expires_at")
