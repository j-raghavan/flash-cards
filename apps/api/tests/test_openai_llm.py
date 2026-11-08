# SPDX-License-Identifier: AGPL-3.0-only
"""Unit tests for the OpenAI-compatible adapter, driven by a mock transport."""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from flashcards.adapters.llm import NullLLMProvider
from flashcards.adapters.openai_llm import OpenAICompatibleLLMProvider
from flashcards.api.app import _llm_from_settings
from flashcards.config import Settings
from flashcards.domain.ai import (
    DRAFT_CARDS_SCHEMA,
    LlmDocument,
    LlmError,
    StructuredRequest,
)

CARDS_JSON = json.dumps({"cards": [{"type": "basic", "fields": {"front": "Q", "back": "A"}}]})


def _body(
    *,
    content: object = CARDS_JSON,
    finish_reason: str = "stop",
    refusal: object = None,
    usage: object = None,
    model: str = "local-model",
    message: object = None,
    choices: object = None,
) -> dict[str, object]:
    if message is None:
        msg: dict[str, object] = {"role": "assistant", "content": content}
        if refusal is not None:
            msg["refusal"] = refusal
        message = msg
    if choices is None:
        choices = [{"finish_reason": finish_reason, "message": message}]
    out: dict[str, object] = {"choices": choices, "model": model}
    if usage is not None:
        out["usage"] = usage
    return out


class _Capture:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def payload(self) -> dict[str, object]:
        parsed = json.loads(self.requests[0].content)
        assert isinstance(parsed, dict)
        return parsed


def _provider(
    response: httpx.Response, *, api_key: str | None = None
) -> tuple[OpenAICompatibleLLMProvider, _Capture]:
    capture = _Capture()

    def handler(request: httpx.Request) -> httpx.Response:
        capture.requests.append(request)
        return response

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleLLMProvider(
        base_url="http://llm.local/v1/", model="local-model", api_key=api_key, client=client
    )
    return provider, capture


def _request(document: LlmDocument) -> StructuredRequest:
    return StructuredRequest(
        instructions="INSTRUCTIONS",
        documents=(document,),
        schema=DRAFT_CARDS_SCHEMA,
        max_output_tokens=1024,
    )


async def test_text_upload_is_data_not_instructions_and_no_tools() -> None:
    usage = {"prompt_tokens": 11, "completion_tokens": 7}
    provider, capture = _provider(httpx.Response(200, json=_body(usage=usage)))

    result = await provider.generate_structured(
        _request(LlmDocument(media_type="text/plain", data=b"SECRET_UPLOAD"))
    )

    assert result.data == {"cards": [{"type": "basic", "fields": {"front": "Q", "back": "A"}}]}
    assert result.usage.input_tokens == 11 and result.usage.output_tokens == 7
    assert result.model == "local-model"

    payload = capture.payload()
    # The endpoint path is base_url + /chat/completions, with the trailing slash trimmed.
    assert str(capture.requests[0].url) == "http://llm.local/v1/chat/completions"
    # NF-SEC-6: instructions are the system message; the upload rides as a user part.
    assert payload["messages"] == [
        {"role": "system", "content": "INSTRUCTIONS"},
        {"role": "user", "content": [{"type": "text", "text": "SECRET_UPLOAD"}]},
    ]
    assert payload["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "result", "schema": DRAFT_CARDS_SCHEMA, "strict": True},
    }
    assert payload["max_tokens"] == 1024
    assert "tools" not in payload


async def test_png_pages_become_data_url_image_parts() -> None:
    provider, capture = _provider(httpx.Response(200, json=_body()))
    await provider.generate_structured(_request(LlmDocument("image/png", b"\x89PNG")))
    encoded = base64.b64encode(b"\x89PNG").decode("ascii")
    assert capture.payload()["messages"] == [
        {"role": "system", "content": "INSTRUCTIONS"},
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}}
            ],
        },
    ]


async def test_jpeg_pages_become_data_url_image_parts() -> None:
    provider, capture = _provider(httpx.Response(200, json=_body()))
    await provider.generate_structured(_request(LlmDocument("image/jpeg", b"\xff\xd8\xff")))
    encoded = base64.b64encode(b"\xff\xd8\xff").decode("ascii")
    assert capture.payload()["messages"] == [
        {"role": "system", "content": "INSTRUCTIONS"},
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}}
            ],
        },
    ]


async def test_pdf_document_is_unsupported() -> None:
    provider, _ = _provider(httpx.Response(200, json=_body()))
    with pytest.raises(LlmError):
        await provider.generate_structured(_request(LlmDocument("application/pdf", b"%PDF-1.4")))


async def test_unsupported_media_type_raises() -> None:
    provider, _ = _provider(httpx.Response(200, json=_body()))
    with pytest.raises(LlmError):
        await provider.generate_structured(_request(LlmDocument("image/gif", b"GIF89a")))


async def test_api_key_sets_bearer_header() -> None:
    provider, capture = _provider(httpx.Response(200, json=_body()), api_key="sk-local")
    await provider.generate_structured(_request(LlmDocument("text/plain", b"x")))
    assert capture.requests[0].headers["authorization"] == "Bearer sk-local"


async def test_no_api_key_sends_no_auth_header() -> None:
    provider, capture = _provider(httpx.Response(200, json=_body()))
    await provider.generate_structured(_request(LlmDocument("text/plain", b"x")))
    assert "authorization" not in capture.requests[0].headers


async def test_usage_defaults_to_zero_when_absent() -> None:
    provider, _ = _provider(httpx.Response(200, json=_body(usage=None)))
    result = await provider.generate_structured(_request(LlmDocument("text/plain", b"x")))
    assert result.usage.input_tokens == 0 and result.usage.output_tokens == 0


async def test_usage_non_integer_tokens_default_to_zero() -> None:
    provider, _ = _provider(httpx.Response(200, json=_body(usage={"prompt_tokens": "lots"})))
    result = await provider.generate_structured(_request(LlmDocument("text/plain", b"x")))
    assert result.usage.input_tokens == 0 and result.usage.output_tokens == 0


async def test_model_falls_back_when_response_omits_it() -> None:
    body = _body()
    del body["model"]
    provider, _ = _provider(httpx.Response(200, json=body))
    result = await provider.generate_structured(_request(LlmDocument("text/plain", b"x")))
    assert result.model == "local-model"


async def test_refusal_raises() -> None:
    body = _body(content=None, refusal="I won't.")
    provider, _ = _provider(httpx.Response(200, json=body))
    with pytest.raises(LlmError):
        await provider.generate_structured(_request(LlmDocument("text/plain", b"x")))


async def test_truncated_output_raises() -> None:
    provider, _ = _provider(httpx.Response(200, json=_body(finish_reason="length")))
    with pytest.raises(LlmError):
        await provider.generate_structured(_request(LlmDocument("text/plain", b"x")))


async def test_no_choices_raises() -> None:
    provider, _ = _provider(httpx.Response(200, json=_body(choices=[])))
    with pytest.raises(LlmError):
        await provider.generate_structured(_request(LlmDocument("text/plain", b"x")))


async def test_malformed_message_raises() -> None:
    provider, _ = _provider(httpx.Response(200, json=_body(message="not-an-object")))
    with pytest.raises(LlmError):
        await provider.generate_structured(_request(LlmDocument("text/plain", b"x")))


async def test_non_text_content_raises() -> None:
    provider, _ = _provider(httpx.Response(200, json=_body(content=None)))
    with pytest.raises(LlmError):
        await provider.generate_structured(_request(LlmDocument("text/plain", b"x")))


async def test_invalid_json_content_raises() -> None:
    provider, _ = _provider(httpx.Response(200, json=_body(content="not json")))
    with pytest.raises(LlmError):
        await provider.generate_structured(_request(LlmDocument("text/plain", b"x")))


async def test_non_object_json_content_raises() -> None:
    provider, _ = _provider(httpx.Response(200, json=_body(content="[1, 2, 3]")))
    with pytest.raises(LlmError):
        await provider.generate_structured(_request(LlmDocument("text/plain", b"x")))


async def test_body_not_an_object_raises() -> None:
    provider, _ = _provider(httpx.Response(200, json=[1, 2, 3]))
    with pytest.raises(LlmError):
        await provider.generate_structured(_request(LlmDocument("text/plain", b"x")))


async def test_non_json_body_raises() -> None:
    provider, _ = _provider(httpx.Response(200, text="not json at all"))
    with pytest.raises(LlmError):
        await provider.generate_structured(_request(LlmDocument("text/plain", b"x")))


async def test_http_error_becomes_llm_error() -> None:
    provider, _ = _provider(httpx.Response(500, text="server exploded"))
    with pytest.raises(LlmError):
        await provider.generate_structured(_request(LlmDocument("text/plain", b"x")))


def test_llm_from_settings_selects_openai_compatible() -> None:
    from flashcards.adapters.openai_llm import OpenAICompatibleLLMProvider as Provider

    chosen = _llm_from_settings(
        Settings(
            environment="test",
            llm_provider="openai_compatible",
            openai_base_url="http://localhost:1234/v1",
        )
    )
    assert isinstance(chosen, Provider)


def test_llm_from_settings_openai_without_base_url_is_null() -> None:
    chosen = _llm_from_settings(
        Settings(environment="test", llm_provider="openai_compatible")
    )
    assert isinstance(chosen, NullLLMProvider)


def test_llm_from_settings_defaults_to_null_without_a_backend() -> None:
    assert isinstance(_llm_from_settings(Settings(environment="test")), NullLLMProvider)
