# SPDX-License-Identifier: AGPL-3.0-only
"""The AI data-use disclosure (F-LEGAL-2)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from flashcards.api.app import create_app
from flashcards.config import Settings
from flashcards.domain.ai import StructuredRequest, StructuredResult, TokenUsage
from flashcards.domain.disclosure import (
    NO_PROVIDER_STATEMENT,
    PROCESSING_STATEMENT,
    TRAINING_STATEMENT,
    ai_disclosure,
)


class _LLM:
    async def generate_structured(
        self, request: StructuredRequest
    ) -> StructuredResult:  # pragma: no cover - never called here
        return StructuredResult(data={}, usage=TokenUsage(0, 0), model="m")


def test_without_a_provider_ai_is_off_and_nothing_is_sent() -> None:
    disclosure = ai_disclosure(provider_key=None)
    assert disclosure.ai_enabled is False and disclosure.provider is None
    assert disclosure.trains_on_user_content is False
    assert disclosure.statements == (TRAINING_STATEMENT, NO_PROVIDER_STATEMENT)


def test_a_known_provider_has_a_name_and_terms_link() -> None:
    disclosure = ai_disclosure(provider_key="anthropic")
    assert disclosure.ai_enabled is True
    assert disclosure.statements == (TRAINING_STATEMENT, PROCESSING_STATEMENT)
    assert disclosure.provider is not None
    assert disclosure.provider.name == "Anthropic"
    assert disclosure.provider.terms_url == "https://www.anthropic.com/legal/commercial-terms"


def test_the_operator_can_name_a_self_hosted_endpoint_and_link_its_terms() -> None:
    bare = ai_disclosure(provider_key="openai_compatible")
    assert bare.provider is not None
    assert bare.provider.name == "OpenAI-compatible endpoint" and bare.provider.terms_url is None
    named = ai_disclosure(
        provider_key="openai_compatible", name="Our LM Studio", terms_url="https://x/terms"
    )
    assert named.provider is not None
    assert (named.provider.name, named.provider.terms_url) == ("Our LM Studio", "https://x/terms")
    unknown = ai_disclosure(provider_key="other")
    assert unknown.provider is not None and unknown.provider.name == "other"


def test_the_disclosure_is_public_over_http() -> None:
    off = TestClient(create_app(Settings(environment="test")))
    body = off.get("/legal/ai-disclosure").json()
    assert body == {
        "ai_enabled": False,
        "trains_on_user_content": False,
        "statements": [TRAINING_STATEMENT, NO_PROVIDER_STATEMENT],
        "provider": None,
    }

    on = TestClient(
        create_app(
            Settings(environment="test", ai_provider_terms_url="https://example.com/terms"),
            llm=_LLM(),
        )
    )
    body = on.get("/legal/ai-disclosure").json()
    assert body["ai_enabled"] is True
    assert body["provider"] == {"name": "Anthropic", "terms_url": "https://example.com/terms"}
