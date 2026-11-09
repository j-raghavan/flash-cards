# SPDX-License-Identifier: AGPL-3.0-only
"""Custom note types and image occlusion notes

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "note_types",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("fields", sa.JSON(), nullable=False),
        sa.Column("templates", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_note_types_owner_id", "note_types", ["owner_id"])
    # A note type in use cannot be deleted: its notes would lose their templates.
    op.add_column(
        "notes",
        sa.Column(
            "note_type_id",
            sa.String(),
            sa.ForeignKey("note_types.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.create_index("ix_notes_note_type_id", "notes", ["note_type_id"])
    op.add_column("notes", sa.Column("occlusion", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("notes", "occlusion")
    op.drop_index("ix_notes_note_type_id", table_name="notes")
    op.drop_column("notes", "note_type_id")
    op.drop_index("ix_note_types_owner_id", table_name="note_types")
    op.drop_table("note_types")
