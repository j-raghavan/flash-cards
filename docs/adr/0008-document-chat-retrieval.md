# ADR 0008: Lexical retrieval for document chat, with vectors as a later add-on

- **Status:** Accepted
- **Date:** 2026-11-08

## Context

F-AI-8 (P1) asks for chat over a deck's source documents, with answers citing file
and page. The PRD's stack table names `pgvector` for retrieval. Vector retrieval
needs an embedding model, and the default provider (Anthropic) has no embeddings
API. Self-hosters on an OpenAI-compatible server may have one; the default install
would not. The CI Postgres image also does not ship the `vector` extension, and the
project promises that any plain Postgres works for self-hosters (ADR 0004).

A vector-only design would leave document chat broken in the default configuration.

## Decision

- A deck's sources are the stored documents and transcripts of the AI jobs that
  generated into it. On the first question, each source is extracted once with the
  existing extractor and split into passages of at most 1,200 characters, kept with
  their file id and page, in a `document_passages` table (migration 0007). A source
  that cannot be read, or has only image pages, is recorded with an empty marker so
  it is not re-extracted on every question.
- Retrieval is BM25 over the deck's passages, computed in the application. It needs
  no extension, no embedding model and no extra service, so it works in in-memory
  mode and on any Postgres.
- The top six passages go to the model as one numbered data document and the
  question as a second; neither is ever part of the instructions (NF-SEC-6). The
  model answers from the passages only and cites them by number; the domain maps
  each valid number back to its file and page and drops anything else.
- When no passage shares a term with the question, the answer is a plain "not found"
  without calling the model.
- Passages are removed on account deletion.

## Consequences

- Document chat works with every backend and every Postgres today.
- Lexical retrieval misses paraphrases that share no words with the question. Adding
  an `EmbeddingProvider` port with a vector column (pgvector where available) is a
  compatible later step: passages are already stored and can be embedded in place.
- Image-only (scanned) pages are not searchable until their text is captured, for
  example by storing the vision model's transcription during card generation.
- Each chat answer that reaches the model is charged against the daily per-user
  AI-call budget shared with summaries, mock exams and AI-evaluation, and the
  synchronous AI routes share a small concurrency limit so slow model calls cannot
  take every database connection.
- Indexing runs inside the first chat request for a deck, and the extractor still
  rasterizes image-only pages that chat then discards. Moving indexing into the
  generation worker and adding a text-only extraction mode are deferred; each source
  is capped at 2,000 passages and an unreadable source is recorded and skipped, so
  the cost is bounded meanwhile.
