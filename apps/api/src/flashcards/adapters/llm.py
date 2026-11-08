# SPDX-License-Identifier: AGPL-3.0-only
"""LLM provider adapters."""

from __future__ import annotations

from flashcards.domain.ai import LlmError, StructuredRequest, StructuredResult


class NullLLMProvider:
    """Refuses every request: wired when no AI backend is configured.

    A generation job then fails cleanly with an LlmError instead of the server
    pretending it can generate.
    """

    async def generate_structured(self, request: StructuredRequest) -> StructuredResult:
        raise LlmError("AI generation is not configured on this instance")
