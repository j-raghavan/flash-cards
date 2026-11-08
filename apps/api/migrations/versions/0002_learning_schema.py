# SPDX-License-Identifier: AGPL-3.0-only
"""M1 learning schema: decks, notes, cards, card_schedule, review_logs

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "decks",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("parent_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_decks_owner_id", "decks", ["owner_id"])
    op.create_index("ix_decks_parent_id", "decks", ["parent_id"])

    op.create_table(
        "notes",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column(
            "deck_id",
            sa.String(),
            sa.ForeignKey("decks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("note_type", sa.String(), nullable=False),
        sa.Column("fields", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_notes_owner_id", "notes", ["owner_id"])
    op.create_index("ix_notes_deck_id", "notes", ["deck_id"])

    op.create_table(
        "cards",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column(
            "deck_id",
            sa.String(),
            sa.ForeignKey("decks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "note_id",
            sa.String(),
            sa.ForeignKey("notes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_cards_owner_id", "cards", ["owner_id"])
    op.create_index("ix_cards_deck_id", "cards", ["deck_id"])
    op.create_index("ix_cards_note_id", "cards", ["note_id"])

    op.create_table(
        "card_schedule",
        sa.Column(
            "card_id",
            sa.String(),
            sa.ForeignKey("cards.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("step", sa.Integer(), nullable=True),
        sa.Column("stability", sa.Float(), nullable=True),
        sa.Column("difficulty", sa.Float(), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_card_schedule_due_at", "card_schedule", ["due_at"])

    op.create_table(
        "review_logs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column(
            "card_id",
            sa.String(),
            sa.ForeignKey("cards.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=True),
        sa.UniqueConstraint(
            "owner_id", "card_id", "idempotency_key", name="uq_review_owner_card_idempotency"
        ),
    )
    op.create_index("ix_review_logs_owner_id", "review_logs", ["owner_id"])
    op.create_index("ix_review_logs_card_id", "review_logs", ["card_id"])


def downgrade() -> None:
    op.drop_table("review_logs")
    op.drop_table("card_schedule")
    op.drop_table("cards")
    op.drop_table("notes")
    op.drop_table("decks")
