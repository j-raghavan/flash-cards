# SPDX-License-Identifier: AGPL-3.0-only
"""Session devices and activity

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


def downgrade() -> None:
    op.drop_table("session_activity")
    op.drop_column("sessions", "device")
