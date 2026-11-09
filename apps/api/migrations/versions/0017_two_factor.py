# SPDX-License-Identifier: AGPL-3.0-only
"""Two-factor sign-in: authenticator apps, passkeys, recovery codes, challenges

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def _user_id() -> sa.Column[str]:
    return sa.Column(
        "user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "totp_credentials",
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("secret_ciphertext", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_step", sa.BigInteger(), nullable=True),
    )
    op.create_table(
        "recovery_codes",
        sa.Column("id", sa.String(), primary_key=True),
        _user_id(),
        sa.Column("code_hash", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "code_hash"),
    )
    op.create_index("ix_recovery_codes_user_id", "recovery_codes", ["user_id"])
    op.create_table(
        "passkeys",
        sa.Column("id", sa.String(), primary_key=True),
        _user_id(),
        sa.Column("credential_id", sa.LargeBinary(), nullable=False, unique=True),
        sa.Column("public_key", sa.LargeBinary(), nullable=False),
        sa.Column("sign_count", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("transports", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_passkeys_user_id", "passkeys", ["user_id"])
    op.create_table(
        "two_factor_challenges",
        sa.Column("id", sa.String(), primary_key=True),
        _user_id(),
        sa.Column("purpose", sa.String(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("webauthn_challenge", sa.LargeBinary(), nullable=True),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_two_factor_challenges_user_id", "two_factor_challenges", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_two_factor_challenges_user_id", table_name="two_factor_challenges")
    op.drop_table("two_factor_challenges")
    op.drop_index("ix_passkeys_user_id", table_name="passkeys")
    op.drop_table("passkeys")
    op.drop_index("ix_recovery_codes_user_id", table_name="recovery_codes")
    op.drop_table("recovery_codes")
    op.drop_table("totp_credentials")
