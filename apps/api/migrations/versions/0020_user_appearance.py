# SPDX-License-Identifier: AGPL-3.0-only
"""Each user's chosen look: light or dark, and an accent palette

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Additive only: every existing account keeps today's look (follow the
    # device, teal palette) until its owner picks another.
    op.add_column(
        "users", sa.Column("theme", sa.String(), nullable=False, server_default="system")
    )
    op.add_column(
        "users", sa.Column("accent", sa.String(), nullable=False, server_default="teal")
    )


def downgrade() -> None:
    op.drop_column("users", "accent")
    op.drop_column("users", "theme")
