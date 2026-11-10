# SPDX-License-Identifier: AGPL-3.0-only
"""Change how the app looks for the caller (domain/appearance.py)."""

from __future__ import annotations

from dataclasses import replace

from flashcards.domain.appearance import Accent, Appearance, Theme
from flashcards.domain.ports import UserRepository
from flashcards.domain.users import UserNotFound


class AppearanceService:
    def __init__(self, users: UserRepository) -> None:
        self._users = users

    async def update(
        self, *, user_id: str, theme: Theme | None = None, accent: Accent | None = None
    ) -> Appearance:
        """Change the parts given, keep the rest; raises UserNotFound."""
        user = await self._users.get_for_update(user_id)
        if user is None:
            raise UserNotFound(user_id)
        appearance = user.appearance
        if theme is not None:
            appearance = replace(appearance, theme=theme)
        if accent is not None:
            appearance = replace(appearance, accent=accent)
        await self._users.update(user.with_appearance(appearance))
        return appearance
