# SPDX-License-Identifier: AGPL-3.0-only
"""LLM provider adapters."""

from __future__ import annotations

import asyncio

from flashcards.domain.ai import (
    DRAFT_CARDS_SCHEMA,
    LlmError,
    StructuredRequest,
    StructuredResult,
    TokenUsage,
)


class NullLLMProvider:
    """Refuses every request: wired when no AI backend is configured.

    A generation job then fails cleanly with an LlmError instead of the server
    pretending it can generate.
    """

    async def generate_structured(self, request: StructuredRequest) -> StructuredResult:
        raise LlmError("AI generation is not configured on this instance")


# The drafts the fake provider returns for every page: one Basic, one Cloze.
FAKE_DRAFT_CARDS: tuple[tuple[str, dict[str, str]], ...] = (
    ("basic", {"front": "What does the mitochondrion produce?", "back": "ATP"}),
    ("cloze", {"text": "Water boils at {{c1::100}} degrees Celsius."}),
)
FAKE_MODEL = "fake-e2e"
# How long the server's fake provider takes per page (see FakeLLMProvider).
FAKE_LATENCY_SECONDS = 1.0


class FakeLLMProvider:
    """Answers card generation with fixed drafts, for the end-to-end tests only.

    Selected by FLASHCARDS_LLM_PROVIDER=fake, which Settings refuses outside the
    development and test environments and in SQL mode (ADR 0021). It reads
    nothing from the request but its schema; every other AI feature is refused,
    as with no provider configured.

    `latency_seconds` holds each answer back, as a real model would, so a test
    can watch a multi-page job's live progress before it finishes.
    """

    def __init__(self, *, latency_seconds: float = 0.0) -> None:
        self._latency = latency_seconds

    async def generate_structured(self, request: StructuredRequest) -> StructuredResult:
        if request.schema != DRAFT_CARDS_SCHEMA:
            raise LlmError("the fake AI provider only drafts cards")
        await asyncio.sleep(self._latency)
        cards: list[object] = [
            {"type": note_type, "fields": dict(fields)} for note_type, fields in FAKE_DRAFT_CARDS
        ]
        return StructuredResult(
            data={"cards": cards},
            usage=TokenUsage(input_tokens=0, output_tokens=0),
            model=FAKE_MODEL,
        )
