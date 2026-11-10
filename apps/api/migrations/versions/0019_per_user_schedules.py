# SPDX-License-Identifier: AGPL-3.0-only
"""Card schedules per user and card (ADR 0024)

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Every existing schedule is its card owner's: the only reviewer there was.
    op.add_column("card_schedule", sa.Column("user_id", sa.String(), nullable=True))
    op.execute(
        "UPDATE card_schedule AS s SET user_id = c.owner_id FROM cards AS c WHERE c.id = s.card_id"
    )
    op.alter_column("card_schedule", "user_id", nullable=False)
    op.drop_constraint("card_schedule_pkey", "card_schedule", type_="primary")
    op.create_primary_key("card_schedule_pkey", "card_schedule", ["user_id", "card_id"])
    # Deleting a card removes every user's row; the due forecast reads one user's rows.
    op.create_index("ix_card_schedule_card_id", "card_schedule", ["card_id"])
    op.create_index("ix_card_schedule_user_due", "card_schedule", ["user_id", "due_at"])
    op.drop_index("ix_card_schedule_due_at", table_name="card_schedule")


def downgrade() -> None:
    # The old key holds one schedule per card, so another user's progress cannot
    # survive it. Refuse rather than drop anyone's review state silently.
    others = op.get_bind().scalar(
        sa.text(
            "SELECT count(*) FROM card_schedule AS s JOIN cards AS c ON c.id = s.card_id "
            "WHERE s.user_id <> c.owner_id"
        )
    )
    if others:
        raise RuntimeError(
            f"{others} card schedules belong to users other than the card's owner; "
            "downgrading would drop them. Restore the backup taken before the upgrade instead."
        )
    op.create_index("ix_card_schedule_due_at", "card_schedule", ["due_at"])
    op.drop_index("ix_card_schedule_user_due", table_name="card_schedule")
    op.drop_index("ix_card_schedule_card_id", table_name="card_schedule")
    op.drop_constraint("card_schedule_pkey", "card_schedule", type_="primary")
    op.create_primary_key("card_schedule_pkey", "card_schedule", ["card_id"])
    op.drop_column("card_schedule", "user_id")
