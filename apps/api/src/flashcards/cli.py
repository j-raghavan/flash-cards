# SPDX-License-Identifier: AGPL-3.0-only
"""Management CLI.

    flashcards create-invite --role admin
        Mint an invite (e.g. the first admin's) and print its token, shown once.
    flashcards generate-totp-key
        Print a new key for FLASHCARDS_TOTP_ENCRYPTION_KEYS (ADR 0018).
    flashcards rotate-totp-key
        Re-encrypt every stored authenticator secret with the first configured key.

create-invite and rotate-totp-key need FLASHCARDS_DATABASE_URL.
"""

from __future__ import annotations

import argparse
import asyncio

from cryptography.fernet import Fernet

from flashcards.adapters.db import create_engine, create_sessionmaker
from flashcards.adapters.memory import SystemClock
from flashcards.adapters.sql import SqlInviteRepository
from flashcards.adapters.sql_two_factor import SqlTwoFactorRepository
from flashcards.adapters.two_factor import FernetSecretBox, parse_keys
from flashcards.application.invite_service import InviteService
from flashcards.config import Settings
from flashcards.domain.invites import Role


async def create_invite(settings: Settings, role: Role, email: str | None) -> str:
    if not settings.database_url:
        raise SystemExit("FLASHCARDS_DATABASE_URL must be set to create a persistent invite.")
    engine = create_engine(settings.database_url)
    try:
        sessionmaker = create_sessionmaker(engine)
        async with sessionmaker() as session, session.begin():
            created = await InviteService(SqlInviteRepository(session), SystemClock()).create(
                created_by="cli", role=role, email=email
            )
        return created.raw_token
    finally:
        await engine.dispose()


def generate_totp_key() -> str:
    return Fernet.generate_key().decode()


async def rotate_totp_key(settings: Settings) -> int:
    """Re-encrypt every authenticator secret with the first key; return how many.

    Every configured key can still decrypt, so this can run while the API serves.
    Once it has run, keys after the first can be removed from the setting.
    """
    keys = parse_keys(settings.totp_encryption_keys or "")
    if not settings.database_url or not keys:
        raise SystemExit(
            "FLASHCARDS_DATABASE_URL and FLASHCARDS_TOTP_ENCRYPTION_KEYS must be set."
        )
    box = FernetSecretBox(keys)
    engine = create_engine(settings.database_url)
    try:
        sessionmaker = create_sessionmaker(engine)
        async with sessionmaker() as session, session.begin():
            repo = SqlTwoFactorRepository(session)
            credentials = await repo.list_totp()
            for credential in credentials:
                await repo.set_totp_secret(
                    credential.user_id, box.reseal(credential.secret_ciphertext)
                )
        return len(credentials)
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="flashcards")
    subparsers = parser.add_subparsers(dest="command", required=True)

    invite = subparsers.add_parser("create-invite", help="create an invite and print its token")
    invite.add_argument("--role", choices=[r.value for r in Role], default=Role.ADMIN.value)
    invite.add_argument("--email", default=None, help="bind the invite to one email address")
    subparsers.add_parser("generate-totp-key", help="print a new TOTP encryption key")
    subparsers.add_parser(
        "rotate-totp-key", help="re-encrypt authenticator secrets with the first key"
    )

    args = parser.parse_args(argv)
    if args.command == "generate-totp-key":
        print(generate_totp_key())
    elif args.command == "rotate-totp-key":
        count = asyncio.run(rotate_totp_key(Settings()))
        print(f"Re-encrypted {count} authenticator secret(s).")
    else:
        token = asyncio.run(create_invite(Settings(), Role(args.role), args.email))
        print(token)


if __name__ == "__main__":
    main()
