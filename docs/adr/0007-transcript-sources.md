# ADR 0007: Video and pasted transcripts as a generation source

- **Status:** Accepted
- **Date:** 2026-11-08

## Context

F-AI-2 (P1) asks for a deck generated from a YouTube link through its transcript,
failing gracefully when no transcript exists. The PRD's risk table already flags
transcript access as unreliable and asks for a manual-paste fallback behind a port.

The M2 generation pipeline (page quota, async worker, draft review, approval) is
built around a stored source plus a `DocumentExtractor` that yields pages. A second,
parallel pipeline for transcripts would duplicate all of that.

## Decision

- A transcript is stored as a small versioned envelope (`FLASHCARDS-TRANSCRIPT/1`
  prefix plus JSON segments of start-second and text) with its own media type.
  `TranscriptAwareExtractor` decorates the PDF/image extractor: an envelope is split
  into chunks of at most 6,000 characters, each yielded as a text page; anything else
  goes to the wrapped extractor. Quota, the worker, drafts and approval are unchanged.
- Each chunk counts as one page for the quota and the 20-page cap, which fits about
  two hours of speech. The whole transcript is capped at 200,000 characters.
- The envelope is decoded defensively (it can also arrive through the generic upload
  route): structure, types, non-negative timestamps and the size cap are all checked.
- Retrieval sits behind a `TranscriptProvider` port. The default adapter,
  `NullTranscriptProvider`, reports every video as having no transcript, so
  `POST /ai/generate/youtube` answers 422 with a pointer to the paste route. No
  third-party transcript library is added for now; a real fetcher is one more adapter.
- `POST /ai/generate/transcript` takes pasted text and feeds the same job.
- For F-AI-5, a card's source page is its chunk number, and for videos the chunk text
  sent to the model starts with its time range (`[hh:mm:ss - hh:mm:ss]`).

## Consequences

- Transcript generation reuses the whole M2 pipeline with no new tables.
- Until a fetcher adapter is configured, YouTube links always fall back to paste.
- Source references for transcripts point at a chunk rather than an exact second; a
  per-card timestamp would need a schema change to the draft record.
