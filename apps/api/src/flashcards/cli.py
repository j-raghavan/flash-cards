# SPDX-License-Identifier: AGPL-3.0-only
"""Management CLI. Today: minting the first admin invite on a deployed instance.

    flashcards create-invite --role admin

Prints the raw invite token (shown once) which the recipient uses to sign up.
Requires FLASHCARDS_DATABASE_URL so the invite persists.
"""

from __future__ import annotations

import argparse
import asyncio

from flashcards.adapters.db import create_engine, create_sessionmaker
from flashcards.adapters.memory import SystemClock
from flashcards.adapters.sql import SqlInviteRepository
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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="flashcards")
    subparsers = parser.add_subparsers(dest="command", required=True)

    invite = subparsers.add_parser("create-invite", help="create an invite and print its token")
    invite.add_argument("--role", choices=[r.value for r in Role], default=Role.ADMIN.value)
    invite.add_argument("--email", default=None, help="bind the invite to one email address")

    args = parser.parse_args(argv)
    # Only one subcommand today; argparse (required=True) guarantees it.
    token = asyncio.run(create_invite(Settings(), Role(args.role), args.email))
    print(token)


if __name__ == "__main__":
    main()
