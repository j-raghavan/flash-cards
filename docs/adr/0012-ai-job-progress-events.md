# ADR 0012: Progress events and retry for AI generation jobs

- **Status:** Accepted
- **Date:** 2026-11-08

## Context

F-AI-10 (P0) asks for generation to run as an async job with progress events over SSE, and to be retryable and cancellable. NF-PERF-2 asks for streaming progress while a 20-page PDF becomes a draft deck.

Jobs already run asynchronously, record progress (pages done out of total), and honour cancellation. Two things are missing: clients can only poll `GET /ai/jobs/{id}`, and a failed job cannot be retried from the API even though the domain supports it.

The worker runs in the API process today (ADR 0004). It may move to a separate process later, in which case it shares only the database with the API.

## Decision

### Progress events

- `GET /ai/jobs/{job_id}/events` streams Server-Sent Events, using FastAPI's built-in `EventSourceResponse`, so no new dependency is needed.
- **Event types:**
  - `progress` carries the job's current state, the same body as `GET /ai/jobs/{id}`. It is sent on connect and whenever the status, progress or cancel request changes.
  - `done` carries the final state when the job succeeds, fails or is cancelled. The stream then ends.
  - Keep-alive comments are sent while nothing changes, so proxies keep the connection open.
- **Event ids:** each event's id identifies the state it describes. Every event is a full snapshot, so a client that reconnects simply receives the current state. The first event sets a reconnection delay of 3 seconds.
- **How the stream reads state:**
  - The route authenticates the caller and checks that the job is theirs (404 otherwise) in an ordinary request transaction. That transaction ends before streaming starts.
  - The stream then reads the job once a second, each time in its own short transaction. No database connection is held for the life of a stream.
  - This works the same whether the worker runs in the API process or elsewhere.
- **Limits:**
  - A stream ends after 10 minutes, and the browser's `EventSource` reconnects on its own.
  - Each user may hold 4 open streams per API process; more answers 429.
  - A job deleted while it is being watched ends the stream.

### Retry

- `POST /ai/jobs/{job_id}/retry` puts a failed job back in the queue, if it has attempts left (3 in total). It resumes from the pages already generated, so finished pages are not generated or charged again.
- A job that has not failed, or has used every attempt, answers 409. Cancelled jobs are not retried: the user starts a new generation instead.
- Any other illegal job transition also answers 409 rather than 500.

## Consequences

- Progress reaches the client within about a second of the worker recording it, at the cost of one small query per open stream per second. Postgres `LISTEN/NOTIFY` could replace polling if the number of concurrent streams grows.
- The per-user stream limit is counted per API process, so it is approximate when several processes serve the API.
- Clients that cannot use SSE can keep polling `GET /ai/jobs/{id}`.
