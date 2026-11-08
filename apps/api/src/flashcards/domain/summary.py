# SPDX-License-Identifier: AGPL-3.0-only
"""AI summary of a deck's material, gated by a card threshold (F-AI-6).

A summary only unlocks once a deck has enough cards to be worth summarizing.
The schema and parser live here so the domain owns the model's output contract.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from flashcards.domain.ai import LlmError

# Default unlock threshold (F-AI-6: "default >= 20 cards, configurable").
DEFAULT_SUMMARY_UNLOCK_CARDS = 20
SUMMARY_MAX_OUTPUT_TOKENS = 2048
# Bound how much deck material is sent to the model, to cap token cost.
MAX_SUMMARY_SOURCE_CHARS = 100_000


class SummaryLocked(Exception):
    """The deck does not have enough cards for a summary yet."""

    def __init__(self, *, required: int, have: int) -> None:
        self.required = required
        self.have = have
        super().__init__(f"summary needs at least {required} cards; the deck has {have}")


class InvalidSummary(LlmError):
    """The model's summary did not match the schema."""


@dataclass(frozen=True, slots=True)
class DeckSummary:
    summary: str
    key_points: tuple[str, ...]


SUMMARY_SCHEMA: Mapping[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "key_points": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "key_points"],
}


def parse_summary(raw: Mapping[str, object]) -> DeckSummary:
    summary = raw.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise InvalidSummary("summary text is missing")
    points = raw.get("key_points")
    if not isinstance(points, list):
        raise InvalidSummary("key_points is missing or not a list")
    key_points = tuple(
        point.strip() for point in points if isinstance(point, str) and point.strip()
    )
    return DeckSummary(summary=summary.strip(), key_points=key_points)
