# SPDX-License-Identifier: AGPL-3.0-only
"""Anthropic structured-generation adapter (F-AI-1, NF-SEC-6).

Uploaded material is sent as document/image content blocks, never concatenated
into the instructions (which go in the system prompt), no tools are declared,
and the output is constrained to the requested JSON schema. The parsed JSON is
re-validated by the domain before anything is trusted.
"""

from __future__ import annotations

import base64
import json

import anthropic
from anthropic.types import (
    Base64ImageSourceParam,
    Base64PDFSourceParam,
    ContentBlockParam,
    DocumentBlockParam,
    ImageBlockParam,
    JSONOutputFormatParam,
    MessageParam,
    OutputConfigParam,
    PlainTextSourceParam,
)

from flashcards.domain.ai import (
    LlmDocument,
    LlmError,
    OutputTruncated,
    StructuredRequest,
    StructuredResult,
    TokenUsage,
)


class AnthropicLLMProvider:
    def __init__(
        self, *, api_key: str, model: str, client: anthropic.AsyncAnthropic | None = None
    ) -> None:
        self._client = client or anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model

    async def aclose(self) -> None:
        await self._client.close()

    async def generate_structured(self, request: StructuredRequest) -> StructuredResult:
        content: list[ContentBlockParam] = [_to_content_block(d) for d in request.documents]
        messages: list[MessageParam] = [{"role": "user", "content": content}]
        output_config = OutputConfigParam(
            format=JSONOutputFormatParam(type="json_schema", schema=dict(request.schema))
        )
        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=request.max_output_tokens,
                system=request.instructions,  # application-authored; no uploaded content
                messages=messages,
                output_config=output_config,
            )
        except anthropic.AnthropicError as exc:  # transport/API failure
            raise LlmError(f"generation request failed: {exc}") from exc

        if response.stop_reason == "refusal":
            raise LlmError("the model declined the request")
        usage = TokenUsage(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
        if response.stop_reason == "max_tokens":
            # The provider worked and billed the call; the cut-off output is unusable.
            raise OutputTruncated(usage, response.model)

        data = _parse_json_object(_first_text(response))
        return StructuredResult(data=data, usage=usage, model=response.model)


def _to_content_block(document: LlmDocument) -> ContentBlockParam:
    if document.media_type == "text/plain":
        return DocumentBlockParam(
            type="document",
            source=PlainTextSourceParam(
                type="text",
                media_type="text/plain",
                data=document.data.decode("utf-8", "replace"),
            ),
        )
    encoded = base64.b64encode(document.data).decode("ascii")
    if document.media_type == "application/pdf":
        return DocumentBlockParam(
            type="document",
            source=Base64PDFSourceParam(
                type="base64", media_type="application/pdf", data=encoded
            ),
        )
    if document.media_type == "image/png":
        return ImageBlockParam(
            type="image",
            source=Base64ImageSourceParam(type="base64", media_type="image/png", data=encoded),
        )
    if document.media_type == "image/jpeg":
        return ImageBlockParam(
            type="image",
            source=Base64ImageSourceParam(type="base64", media_type="image/jpeg", data=encoded),
        )
    # The extractor only ever yields the media types above; guard the rest.
    raise LlmError(f"unsupported document media type: {document.media_type}")


def _first_text(response: anthropic.types.Message) -> str:
    for block in response.content:
        if block.type == "text":
            return block.text
    raise LlmError("the model returned no text content")


def _parse_json_object(text: str) -> dict[str, object]:
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise LlmError("the model did not return valid JSON") from exc
    if not isinstance(parsed, dict):
        raise LlmError("the model output was not a JSON object")
    return parsed
