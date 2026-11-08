# SPDX-License-Identifier: AGPL-3.0-only
"""Card browser: note tags, card flags and bury

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "notes", sa.Column("tags", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))
    )
    op.add_column(
        "cards", sa.Column("flag", sa.Integer(), nullable=False, server_default=sa.text("0"))
    )
    op.add_column("cards", sa.Column("buried_until", sa.DateTime(timezone=True), nullable=True))
    # Concurrent edits of one note can never create two cards for the same ordinal.
    op.create_unique_constraint("uq_cards_note_ordinal", "cards", ["note_id", "ordinal"])


def downgrade() -> None:
    op.drop_constraint("uq_cards_note_ordinal", "cards", type_="unique")
    op.drop_column("cards", "buried_until")
    op.drop_column("cards", "flag")
    op.drop_column("notes", "tags")
