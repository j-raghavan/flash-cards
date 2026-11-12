# SPDX-License-Identifier: AGPL-3.0-only
"""Let the instance default monthly page limit be unlimited (NULL)

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # NULL now means members have no monthly page limit (ADR 0027). Existing rows
    # keep whatever limit an admin had already set; only the column becomes
    # nullable so "unlimited" can be stored.
    op.alter_column("instance_quotas", "default_monthly_pages", nullable=True)


def downgrade() -> None:
    # An "unlimited" (NULL) default has no finite equivalent; fall back to the old
    # shipped default of 10 so the column can be NOT NULL again.
    op.execute(
        sa.text(
            "UPDATE instance_quotas SET default_monthly_pages = 10 "
            "WHERE default_monthly_pages IS NULL"
        )
    )
    op.alter_column("instance_quotas", "default_monthly_pages", nullable=False)
