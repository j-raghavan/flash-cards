# SPDX-License-Identifier: AGPL-3.0-only
"""How the app looks for one user: light or dark, and an accent palette.

Saved on the account, so the choice follows the user to every device. A new
account, and every account made before the choice existed, follows the
device's own light or dark setting with the teal palette.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Theme(StrEnum):
    SYSTEM = "system"  # follow the device's light or dark setting
    LIGHT = "light"
    DARK = "dark"


class Accent(StrEnum):
    TEAL = "teal"
    INDIGO = "indigo"
    ROSE = "rose"
    AMBER = "amber"


@dataclass(frozen=True, slots=True)
class Appearance:
    theme: Theme = Theme.SYSTEM
    accent: Accent = Accent.TEAL
