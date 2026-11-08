# SPDX-License-Identifier: AGPL-3.0-only
"""Sign-in throttling schema: login_throttle

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "login_throttle",
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("login_throttle")
