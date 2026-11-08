# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import pytest

from flashcards.adapters.llm import NullLLMProvider
from flashcards.domain.ai import DRAFT_CARDS_SCHEMA, LlmError, StructuredRequest


async def test_null_llm_refuses() -> None:
    request = StructuredRequest(
        instructions="x", documents=(), schema=DRAFT_CARDS_SCHEMA, max_output_tokens=10
    )
    with pytest.raises(LlmError):
        await NullLLMProvider().generate_structured(request)
