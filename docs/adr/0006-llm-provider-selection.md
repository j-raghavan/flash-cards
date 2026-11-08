# ADR 0006: LLM provider selection and an OpenAI-compatible adapter

- **Status:** Accepted
- **Date:** 2026-11-08

## Context

[ADR 0004](0004-phased-hosting.md) put every infrastructure dependency behind a
port, `LLMProvider` among them, so the AI backend can change without touching the
application or domain layers. M2 shipped one implementation, `AnthropicLLMProvider`,
selected by a single rule in `app.py`: if an Anthropic API key is configured, use
it, otherwise wire a `NullLLMProvider` that refuses every request.

The product requirement F-AI-12 (P0) asks for a second implementation that talks to
any OpenAI-compatible chat-completions endpoint, which covers the common local-model
servers (LM Studio, Ollama, vLLM). This lets a self-hoster run the whole product
against a model on their own machine, with no per-token cost and no data leaving
their network. It also means provider selection can no longer be inferred from the
presence of one key.

## Decision

Add an explicit provider selector and a second adapter.

- A new setting `llm_provider` selects the backend: `anthropic` (the default) or
  `openai_compatible`. Selection is explicit rather than inferred from which keys
  happen to be set, so an operator always knows which backend is live.
- `OpenAICompatibleLLMProvider` (a new adapter) implements the same `LLMProvider`
  port by calling `POST {base_url}/chat/completions` over `httpx`:
  - Instructions go in a `system` message; uploaded material rides as user-message
    content parts, never concatenated into the instructions (NF-SEC-6, same rule the
    Anthropic adapter follows).
  - Structured output is constrained with `response_format` of type `json_schema`
    carrying the domain-owned schema with `strict: true`. No tools are declared.
  - Text pages become `text` parts; PNG and JPEG pages become `image_url` parts with
    a base64 `data:` URL. The chat-completions API has no portable whole-PDF input
    across these local servers, and the extractor already rasterizes non-text PDF
    pages to PNG, so a raw `application/pdf` document raises `LlmError`.
  - A truncated response (`finish_reason == "length"`), an explicit refusal, a
    non-text message, a missing choice, or any transport or non-2xx error all raise
    `LlmError`. The parsed JSON is still re-validated by the domain before anything
    is trusted.
- When `llm_provider` is `openai_compatible` but no `openai_base_url` is set, the
  server wires `NullLLMProvider` (same clean-refusal behaviour as a missing
  Anthropic key) rather than starting up broken.
- `httpx` becomes an explicit runtime dependency. It was already present transitively
  through `anthropic`; declaring it directly is honest about the new use.

New settings (env prefix `FLASHCARDS_`): `llm_provider`, `openai_base_url`,
`openai_api_key` (optional; many local servers ignore it), `openai_model`.

## Consequences

- Self-hosters can run entirely on a local model, which matches the project's
  local-model-friendly, privacy-first posture.
- The domain's single structured-output contract (`StructuredRequest`,
  `generate_structured`, a domain-owned JSON Schema) now has two interchangeable
  implementations, so every later AI feature (summaries, mock exams, AI-evaluation)
  works against either backend unchanged.
- Provider selection is one explicit setting, so misconfiguration fails loudly as a
  refusing provider rather than a half-working one.
- PDF handling differs by backend (Anthropic takes PDFs directly, the
  OpenAI-compatible path does not). In practice the extractor feeds per-page text and
  images, so this does not affect the card-generation flow.
