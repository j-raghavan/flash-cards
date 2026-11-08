# SPDX-License-Identifier: AGPL-3.0-only
"""OpenAI-compatible structured-generation adapter (F-AI-12, NF-SEC-6).

Talks to any OpenAI-compatible chat-completions endpoint (LM Studio, Ollama,
vLLM, ...). Uploaded material is sent as user-message content parts, never
concatenated into the instructions (which go in the system message), no tools
are declared, and the output is constrained to the requested JSON schema with
``response_format``. The parsed JSON is re-validated by the domain before
anything is trusted.
"""

from __future__ import annotations

import base64
import json

import httpx

from flashcards.domain.ai import (
    LlmDocument,
    LlmError,
    StructuredRequest,
    StructuredResult,
    TokenUsage,
)

_DEFAULT_TIMEOUT = 120.0


class OpenAICompatibleLLMProvider:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._client = client or httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT)
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    async def generate_structured(self, request: StructuredRequest) -> StructuredResult:
        content = [_to_content_part(document) for document in request.documents]
        payload = {
            "model": self._model,
            "max_tokens": request.max_output_tokens,
            "messages": [
                {"role": "system", "content": request.instructions},
                {"role": "user", "content": content},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "result",
                    "schema": dict(request.schema),
                    "strict": True,
                },
            },
        }
        try:
            response = await self._client.post(
                f"{self._base_url}/chat/completions", json=payload, headers=self._headers
            )
            response.raise_for_status()
            raw: object = response.json()
        except httpx.HTTPError as exc:  # transport failure or non-2xx status
            raise LlmError(f"generation request failed: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise LlmError("the endpoint did not return JSON") from exc
        return _parse_response(raw, fallback_model=self._model)


def _to_content_part(document: LlmDocument) -> dict[str, object]:
    if document.media_type == "text/plain":
        return {"type": "text", "text": document.data.decode("utf-8", "replace")}
    if document.media_type in ("image/png", "image/jpeg"):
        encoded = base64.b64encode(document.data).decode("ascii")
        url = f"data:{document.media_type};base64,{encoded}"
        return {"type": "image_url", "image_url": {"url": url}}
    # OpenAI-compatible chat endpoints have no portable whole-PDF input; the
    # extractor rasterizes PDF pages to PNG, so this path should not be reached.
    raise LlmError(f"unsupported document media type: {document.media_type}")


def _parse_response(raw: object, *, fallback_model: str) -> StructuredResult:
    body = _require_object(raw, "response body")
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LlmError("the endpoint returned no choices")
    choice = _require_object(choices[0], "choice")
    if choice.get("finish_reason") == "length":
        raise LlmError("the model output was truncated")
    message = _require_object(choice.get("message"), "message")
    if message.get("refusal"):
        raise LlmError("the model declined the request")
    text = message.get("content")
    if not isinstance(text, str) or not text:
        raise LlmError("the model returned no text content")
    data = _parse_json_object(text)
    usage = _parse_usage(body.get("usage"))
    model = body.get("model")
    return StructuredResult(
        data=data, usage=usage, model=model if isinstance(model, str) else fallback_model
    )


def _parse_usage(value: object) -> TokenUsage:
    if not isinstance(value, dict):
        return TokenUsage(input_tokens=0, output_tokens=0)
    return TokenUsage(
        input_tokens=_as_int(value.get("prompt_tokens")),
        output_tokens=_as_int(value.get("completion_tokens")),
    )


def _as_int(value: object) -> int:
    return value if isinstance(value, int) else 0


def _require_object(value: object, what: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise LlmError(f"malformed {what} in response")
    return value


def _parse_json_object(text: str) -> dict[str, object]:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LlmError("the model did not return valid JSON") from exc
    if not isinstance(parsed, dict):
        raise LlmError("the model output was not a JSON object")
    return parsed
