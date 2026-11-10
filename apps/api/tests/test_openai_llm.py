# SPDX-License-Identifier: AGPL-3.0-only
"""Unit tests for the OpenAI-compatible adapter, driven by a mock transport."""

from __future__ import annotations

import base64
import json
from typing import get_args

import httpx
import pytest
from pydantic import ValidationError
from pydantic_settings import SettingsConfigDict

from flashcards.adapters.llm import NullLLMProvider
from flashcards.adapters.openai_llm import (
    OpenAICompatibleLLMProvider,
    OutputLimitField,
    ReasoningEffort,
)
from flashcards.api.app import _llm_from_settings
from flashcards.config import Settings
from flashcards.domain.ai import (
    DRAFT_CARDS_SCHEMA,
    InvalidModelOutput,
    LlmDocument,
    LlmError,
    OutputTruncated,
    StructuredRequest,
    TokenUsage,
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
    response: httpx.Response,
    *,
    api_key: str | None = None,
    output_limit_field: OutputLimitField = "max_tokens",
    reasoning_effort: ReasoningEffort | None = None,
) -> tuple[OpenAICompatibleLLMProvider, _Capture]:
    capture = _Capture()

    def handler(request: httpx.Request) -> httpx.Response:
        capture.requests.append(request)
        return response

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleLLMProvider(
        base_url="http://llm.local/v1/",
        model="local-model",
        api_key=api_key,
        output_limit_field=output_limit_field,
        reasoning_effort=reasoning_effort,
        client=client,
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
    assert "max_completion_tokens" not in payload
    assert "reasoning_effort" not in payload
    assert "tools" not in payload


async def test_reasoning_models_get_max_completion_tokens_and_an_effort() -> None:
    provider, capture = _provider(
        httpx.Response(200, json=_body()),
        output_limit_field="max_completion_tokens",
        reasoning_effort="low",
    )

    await provider.generate_structured(
        _request(LlmDocument(media_type="text/plain", data=b"notes"))
    )

    payload = capture.payload()
    assert payload["max_completion_tokens"] == 1024
    assert "max_tokens" not in payload
    assert payload["reasoning_effort"] == "low"


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


async def test_truncated_output_is_unusable_output_not_a_provider_failure() -> None:
    usage = {"prompt_tokens": 11, "completion_tokens": 7}
    body = _body(finish_reason="length", usage=usage)
    provider, _ = _provider(httpx.Response(200, json=body))
    with pytest.raises(OutputTruncated, match="output limit") as cut:
        await provider.generate_structured(_request(LlmDocument("text/plain", b"x")))
    assert isinstance(cut.value, InvalidModelOutput)
    assert cut.value.usage == TokenUsage(11, 7)


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


async def test_http_error_becomes_llm_error_without_the_url() -> None:
    provider, _ = _provider(httpx.Response(500, text="server exploded"))
    with pytest.raises(LlmError) as caught:
        await provider.generate_structured(_request(LlmDocument("text/plain", b"x")))
    # The message can reach users (a failed job's error), so no endpoint URL in it.
    assert str(caught.value) == "the model endpoint returned HTTP 500"
    assert "llm.local" not in str(caught.value)


async def test_unreachable_endpoint_is_a_generic_llm_error() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused to http://10.0.0.5:1234", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(refuse))
    provider = OpenAICompatibleLLMProvider(
        base_url="http://10.0.0.5:1234/v1", model="m", client=client
    )
    with pytest.raises(LlmError) as caught:
        await provider.generate_structured(_request(LlmDocument("text/plain", b"x")))
    assert str(caught.value) == "could not reach the model endpoint"


async def test_non_utf8_body_is_an_llm_error() -> None:
    provider, _ = _provider(httpx.Response(200, content=b"\x80\x81 not utf-8"))
    with pytest.raises(LlmError):
        await provider.generate_structured(_request(LlmDocument("text/plain", b"x")))


async def test_oversized_body_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("flashcards.adapters.openai_llm.MAX_RESPONSE_BYTES", 10)
    provider, _ = _provider(httpx.Response(200, json=_body()))
    with pytest.raises(LlmError, match="oversized"):
        await provider.generate_structured(_request(LlmDocument("text/plain", b"x")))


async def test_content_parts_list_is_joined() -> None:
    parts = [
        {"type": "text", "text": CARDS_JSON[:10]},
        {"type": "image_url", "image_url": {"url": "data:,"}},
        "junk",
        {"type": "text", "text": 5},
        {"type": "text", "text": CARDS_JSON[10:]},
    ]
    provider, _ = _provider(httpx.Response(200, json=_body(content=parts)))
    result = await provider.generate_structured(_request(LlmDocument("text/plain", b"x")))
    assert "cards" in result.data


async def test_boolean_token_counts_are_ignored() -> None:
    usage = {"prompt_tokens": True, "completion_tokens": 3}
    provider, _ = _provider(httpx.Response(200, json=_body(usage=usage)))
    result = await provider.generate_structured(_request(LlmDocument("text/plain", b"x")))
    assert result.usage.input_tokens == 0 and result.usage.output_tokens == 3


async def test_timeout_is_configurable_and_the_client_closes() -> None:
    provider = OpenAICompatibleLLMProvider(
        base_url="http://llm.local/v1", model="m", timeout_seconds=7.5
    )
    assert provider._client.timeout.read == 7.5
    await provider.aclose()
    assert provider._client.is_closed


class _HermeticSettings(Settings):
    """Settings that ignore real FLASHCARDS_* env vars and .env, for hermetic tests."""

    model_config = SettingsConfigDict(
        env_prefix="FLASHCARDS_TEST_UNSET_", env_file=None, extra="ignore"
    )


def test_an_unknown_provider_fails_at_startup() -> None:
    with pytest.raises(ValidationError):
        _HermeticSettings.model_validate({"llm_provider": "openai"})


def test_llm_from_settings_selects_openai_compatible() -> None:
    from flashcards.adapters.openai_llm import OpenAICompatibleLLMProvider as Provider

    chosen = _llm_from_settings(
        _HermeticSettings(
            llm_provider="openai_compatible", openai_base_url="http://localhost:1234/v1"
        )
    )
    assert isinstance(chosen, Provider)
    assert chosen._output_limit_field == "max_tokens"
    assert chosen._reasoning_effort is None


def test_llm_from_settings_passes_the_reasoning_model_options(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from flashcards.adapters.openai_llm import OpenAICompatibleLLMProvider as Provider

    chosen = _llm_from_settings(
        _HermeticSettings(
            llm_provider="openai_compatible",
            openai_base_url="https://api.openai.com/v1",
            openai_output_limit_field="max_completion_tokens",
            openai_reasoning_effort="minimal",
        )
    )
    assert isinstance(chosen, Provider)
    assert chosen._output_limit_field == "max_completion_tokens"
    assert chosen._reasoning_effort == "minimal"
    assert "openai_reasoning_effort is not set" not in caplog.text


def test_max_completion_tokens_without_an_effort_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    _llm_from_settings(
        _HermeticSettings(
            llm_provider="openai_compatible",
            openai_base_url="https://api.openai.com/v1",
            openai_output_limit_field="max_completion_tokens",
        )
    )
    assert "openai_reasoning_effort is not set" in caplog.text


def test_settings_and_adapter_accept_the_same_values() -> None:
    fields = Settings.model_fields
    assert get_args(fields["openai_output_limit_field"].annotation) == get_args(OutputLimitField)
    effort = get_args(fields["openai_reasoning_effort"].annotation)
    assert len(effort) == 2 and effort[1] is type(None)
    assert get_args(effort[0]) == get_args(ReasoningEffort)


def test_an_unknown_reasoning_effort_fails_at_startup() -> None:
    with pytest.raises(ValidationError):
        _HermeticSettings.model_validate({"openai_reasoning_effort": "extreme"})


def test_llm_from_settings_openai_without_base_url_is_null_and_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    chosen = _llm_from_settings(_HermeticSettings(llm_provider="openai_compatible"))
    assert isinstance(chosen, NullLLMProvider)
    assert "openai_base_url is not set" in caplog.text


def test_llm_from_settings_defaults_to_null_without_a_backend(
    caplog: pytest.LogCaptureFixture,
) -> None:
    assert isinstance(_llm_from_settings(_HermeticSettings()), NullLLMProvider)
    assert "No AI backend is configured" in caplog.text
