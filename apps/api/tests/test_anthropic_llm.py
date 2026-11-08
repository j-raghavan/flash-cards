# SPDX-License-Identifier: AGPL-3.0-only
"""Unit tests for the Anthropic adapter, driven by a fake client (no network)."""

from __future__ import annotations

import base64
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

import anthropic
import pytest
from anthropic.types import MessageParam, OutputConfigParam
from pydantic_settings import SettingsConfigDict

from flashcards.adapters.anthropic_llm import AnthropicLLMProvider
from flashcards.adapters.llm import NullLLMProvider
from flashcards.api.app import _llm_from_settings
from flashcards.config import Settings
from flashcards.domain.ai import (
    DRAFT_CARDS_SCHEMA,
    LlmDocument,
    LlmError,
    StructuredRequest,
)

CARDS_JSON = json.dumps({"cards": [{"type": "basic", "fields": {"front": "Q", "back": "A"}}]})


class _Block:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _NonTextBlock:
    def __init__(self) -> None:
        self.type = "thinking"


class _Usage:
    input_tokens = 11
    output_tokens = 7


class _Response:
    def __init__(
        self, blocks: Sequence[_Block | _NonTextBlock], stop_reason: str | None = None
    ) -> None:
        self.content = blocks
        self.stop_reason = stop_reason
        self.usage = _Usage()
        self.model = "claude-opus-5-5"


@dataclass(frozen=True)
class _CapturedCall:
    model: str
    max_tokens: int
    system: str
    messages: list[MessageParam]
    output_config: OutputConfigParam


class _Messages:
    def __init__(self, response: _Response | None, error: Exception | None) -> None:
        self._response = response
        self._error = error
        self.calls: list[_CapturedCall] = []

    async def create(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str,
        messages: list[MessageParam],
        output_config: OutputConfigParam,
    ) -> _Response | None:
        # The exact keyword set the provider is allowed to send: an extra kwarg
        # (e.g. tools) would raise here, so "no tools" is enforced structurally.
        self.calls.append(
            _CapturedCall(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
                output_config=output_config,
            )
        )
        if self._error is not None:
            raise self._error
        return self._response


class _Client:
    def __init__(self, response: _Response | None = None, error: Exception | None = None) -> None:
        self.messages = _Messages(response, error)


def _provider(
    response: _Response | None = None, error: Exception | None = None
) -> tuple[AnthropicLLMProvider, _Client]:
    client = _Client(response, error)
    provider = AnthropicLLMProvider(
        api_key="unused", model="claude-opus-5-5", client=cast(anthropic.AsyncAnthropic, client)
    )
    return provider, client


def _request(document: LlmDocument) -> StructuredRequest:
    return StructuredRequest(
        instructions="INSTRUCTIONS",
        documents=(document,),
        schema=DRAFT_CARDS_SCHEMA,
        max_output_tokens=1024,
    )


async def test_uploads_go_as_data_not_instructions_and_no_tools() -> None:
    provider, client = _provider(_Response([_Block(CARDS_JSON)]))
    request = _request(LlmDocument(media_type="text/plain", data=b"SECRET_UPLOAD"))

    result = await provider.generate_structured(request)

    assert result.data == {"cards": [{"type": "basic", "fields": {"front": "Q", "back": "A"}}]}
    assert result.usage.input_tokens == 11 and result.usage.output_tokens == 7

    call = client.messages.calls[0]
    # NF-SEC-6: the prompt is the system message; the upload rides as a document block
    # in the user message, never concatenated into the instructions.
    assert call.system == "INSTRUCTIONS"
    assert call.messages == [
        {
            "role": "user",
            "content": [
                {
                    "type": "document",
                    "source": {
                        "type": "text",
                        "media_type": "text/plain",
                        "data": "SECRET_UPLOAD",
                    },
                }
            ],
        }
    ]
    assert call.output_config == {
        "format": {"type": "json_schema", "schema": DRAFT_CARDS_SCHEMA}
    }


async def test_image_pages_become_base64_image_blocks() -> None:
    provider, client = _provider(_Response([_Block(CARDS_JSON)]))
    request = _request(LlmDocument(media_type="image/png", data=b"\x89PNG"))

    await provider.generate_structured(request)

    encoded = base64.b64encode(b"\x89PNG").decode("ascii")
    assert client.messages.calls[0].messages == [
        {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": encoded},
                }
            ],
        }
    ]


async def test_pdf_pages_become_base64_document_blocks() -> None:
    provider, client = _provider(_Response([_Block(CARDS_JSON)]))
    await provider.generate_structured(_request(LlmDocument("application/pdf", b"%PDF-1.4")))
    encoded = base64.b64encode(b"%PDF-1.4").decode("ascii")
    assert client.messages.calls[0].messages == [
        {
            "role": "user",
            "content": [
                {
                    "type": "document",
                    "source": {"type": "base64", "media_type": "application/pdf", "data": encoded},
                }
            ],
        }
    ]


async def test_jpeg_pages_become_base64_image_blocks() -> None:
    provider, client = _provider(_Response([_Block(CARDS_JSON)]))
    await provider.generate_structured(_request(LlmDocument("image/jpeg", b"\xff\xd8\xff")))
    encoded = base64.b64encode(b"\xff\xd8\xff").decode("ascii")
    assert client.messages.calls[0].messages == [
        {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/jpeg", "data": encoded},
                }
            ],
        }
    ]


async def test_unsupported_media_type_raises() -> None:
    provider, _ = _provider(_Response([_Block(CARDS_JSON)]))
    with pytest.raises(LlmError):
        await provider.generate_structured(_request(LlmDocument("image/gif", b"GIF89a")))


async def test_first_text_skips_non_text_blocks() -> None:
    provider, _ = _provider(_Response([_NonTextBlock(), _Block(CARDS_JSON)]))
    result = await provider.generate_structured(_request(LlmDocument("text/plain", b"x")))
    assert "cards" in result.data


async def test_refusal_raises() -> None:
    provider, _ = _provider(_Response([_Block(CARDS_JSON)], stop_reason="refusal"))
    with pytest.raises(LlmError):
        await provider.generate_structured(_request(LlmDocument("text/plain", b"x")))


async def test_no_text_block_raises() -> None:
    provider, _ = _provider(_Response([]))
    with pytest.raises(LlmError):
        await provider.generate_structured(_request(LlmDocument("text/plain", b"x")))


async def test_invalid_json_raises() -> None:
    provider, _ = _provider(_Response([_Block("not json")]))
    with pytest.raises(LlmError):
        await provider.generate_structured(_request(LlmDocument("text/plain", b"x")))


async def test_non_object_json_raises() -> None:
    provider, _ = _provider(_Response([_Block("[1, 2, 3]")]))
    with pytest.raises(LlmError):
        await provider.generate_structured(_request(LlmDocument("text/plain", b"x")))


async def test_transport_error_becomes_llm_error() -> None:
    provider, _ = _provider(error=anthropic.AnthropicError("boom"))
    with pytest.raises(LlmError):
        await provider.generate_structured(_request(LlmDocument("text/plain", b"x")))


class _HermeticSettings(Settings):
    """Settings that ignore real FLASHCARDS_* env vars and .env, for hermetic tests."""

    model_config = SettingsConfigDict(
        env_prefix="FLASHCARDS_TEST_UNSET_", env_file=None, extra="ignore"
    )


def test_llm_from_settings_selects_by_key() -> None:
    assert isinstance(_llm_from_settings(_HermeticSettings()), NullLLMProvider)
    configured = _llm_from_settings(_HermeticSettings(anthropic_api_key="sk-test"))
    assert isinstance(configured, AnthropicLLMProvider)


class _ClosableClient(_Client):
    def __init__(self) -> None:
        super().__init__()
        self.closed = False

    async def close(self) -> None:
        self.closed = True


async def test_aclose_closes_the_sdk_client() -> None:
    client = _ClosableClient()
    provider = AnthropicLLMProvider(
        api_key="unused", model="claude-opus-5-5", client=cast(anthropic.AsyncAnthropic, client)
    )
    await provider.aclose()
    assert client.closed
