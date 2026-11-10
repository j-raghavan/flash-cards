# SPDX-License-Identifier: AGPL-3.0-only
"""Who may read whose study material (ADR 0024).

Every deck owned by an active admin is part of the shared library: any signed-in
user may read and study it, while only its owner may change it. A ReadScope is
that rule resolved for one caller at one moment.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReadScope:
    """The owners whose material the caller may read: themselves and the active admins."""

    user_id: str
    library_owner_ids: frozenset[str]

    def can_read(self, owner_id: str) -> bool:
        return owner_id == self.user_id or owner_id in self.library_owner_ids

    def owns(self, owner_id: str) -> bool:
        return owner_id == self.user_id

    @property
    def owner_ids(self) -> tuple[str, ...]:
        """Every readable owner, the caller included, sorted."""
        return tuple(sorted(self.library_owner_ids | {self.user_id}))
