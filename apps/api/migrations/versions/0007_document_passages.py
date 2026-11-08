# SPDX-License-Identifier: AGPL-3.0-only
"""M5 document-chat schema: document_passages

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_passages",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("deck_id", sa.String(), nullable=False),
        sa.Column("file_id", sa.String(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "deck_id", "file_id", "page", "ordinal", name="uq_document_passages_position"
        ),
    )
    op.create_index("ix_document_passages_owner_id", "document_passages", ["owner_id"])
    op.create_index("ix_document_passages_deck_id", "document_passages", ["deck_id"])


def downgrade() -> None:
    op.drop_table("document_passages")
