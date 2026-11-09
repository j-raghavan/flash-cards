# ADR 0017: Security hardening from the threat model

- **Status:** Accepted
- **Date:** 2026-11-08

## Context

The threat model (`docs/security/threat-model.md`) lists residual risks in the API as built. Six of them are fixed here:

1. Argon2, PDF page counting and `.apkg` reading (zip and SQLite) ran synchronously on the event loop, so one slow upload or a burst of sign-ins stalled every request. Rendering a PDF page trusted the page's declared size.
2. `.apkg` and AI uploads were read fully into memory before the size check.
3. `SameSite=Lax` was the only CSRF control (PRD NF-SEC-4 asks for more).
4. A failed generation job stored and returned raw exception text, including provider SDK messages, without logging it.
5. Jobs queued or running when the process stopped were never picked up again, and kept their reserved quota.
6. `FLASHCARDS_MAX_UPLOAD_PAGES` was documented but not read.

Signup enumeration and throttling are handled separately.

## Decision

### Blocking work runs in bounded worker threads

- The `PasswordHasher`, `DocumentExtractor.count_pages` and `PackageReader` ports are async. Adapters run the blocking work with `anyio.to_thread.run_sync` (`adapters/offload.py`).
- Each kind of work has its own `CapacityLimiter`: 4 concurrent Argon2 operations (each uses 64 MiB, so this also bounds memory), 2 concurrent `.apkg` parses, and 1 slot for all PyMuPDF work (open with the page count, text extraction, rendering, close), because PyMuPDF does not support concurrent use from several threads. A burst waits for a slot instead of taking every thread, and sign-ins, imports and PDFs cannot starve each other.
- A text-less page is rendered at 150 DPI unless that would exceed 16 million pixels; the zoom is then reduced to fit. The size check uses the page rectangle, before anything is allocated.
- PyMuPDF errors become a domain `UnreadableDocument`: 422 at upload, a categorized failure in the worker.
- Parsing still runs inside the API process. A sandboxed worker process with memory and CPU limits remains a follow-up.

### Request bodies and uploads are capped while reading

- `api/body_limit.BodySizeLimitMiddleware`, the outermost middleware, caps every request body at 50 MB (above the 40 MB `.apkg` cap plus multipart overhead, and the same as the bundled Caddyfile). It refuses a declared `Content-Length` over the cap with 413 before reading anything, and counts the body as it streams in, so a chunked or understated request is cut off too. This bounds the temporary files the multipart parser spools to, which happens before authentication, also on deployments without a proxy body limit.
- `api/uploads.read_capped` serves every upload route (AI generate, `.apkg` import, media). It refuses at once when the multipart parser already knows the size is over the route's cap, and otherwise reads 1 MB chunks and answers 413 as soon as the total passes it, so an upload holds at most about twice its cap in memory.

### CSRF: unsafe requests must show a trusted origin

`api/csrf.CsrfOriginMiddleware`, a pure ASGI middleware, checks every POST, PUT, PATCH and DELETE. Safe methods pass untouched. The first rule that applies decides:

| # | Request carries | Accepted when |
|---|---|---|
| 1 | `Origin` other than `null` | it is a trusted origin |
| 2 | `Sec-Fetch-Site` | it is `same-origin` or `none` |
| 3 | `Origin: null` (and no `Sec-Fetch-Site`) | never |
| 4 | `Referer` | its origin is trusted |
| 5 | none of the above | `X-Requested-With` is present |

Anything else answers 403 `{"detail": "Cross-site request refused."}`.

Trusted origins are the origin of `FLASHCARDS_PUBLIC_BASE_URL` (scheme, host and port, compared case-insensitively, with a scheme's default port dropped as browsers do) and the request's own host from its `Host` header. For the own-host comparison the scheme is ignored: behind a TLS-terminating proxy the API sees plain HTTP while the browser sent `https`, and the `Secure`, `SameSite` session cookie already keeps plain-HTTP pages out.

Why this rule:

- Browsers send `Origin` on every cross-origin unsafe request, and `Sec-Fetch-Site` on every request since 2023, so a hostile site, including a sibling subdomain of the same site, is refused by rule 1 or 2.
- When the page's `Referrer-Policy` is `no-referrer` (the bundled Caddyfile sets it), browsers send `Origin: null` on same-origin requests; `Sec-Fetch-Site: same-origin` still lets them through.
- Rule 5 covers clients that send none of these headers. Browsers cannot add `X-Requested-With` to a cross-origin request without a CORS preflight, which this API never approves, so only same-origin scripts and non-browser clients can send it. The web client sends `X-Requested-With: fetch` on every request; scripts and other non-browser clients must send it (or an `Origin` matching the public URL).

In production the web client calls the API under `/api` through the reverse proxy, which strips the prefix, so requests carry the public origin and pass rule 1. In development, with the web client on `localhost:5173` and the API on another port, set `FLASHCARDS_PUBLIC_BASE_URL` to the web client's origin (its default).

Tests do not weaken the check: one autouse fixture in `tests/conftest.py` makes every `TestClient` send `X-Requested-With`, as a non-browser client must, and `tests/test_csrf.py` covers each accepted and refused case with a client that sends nothing by default.

### Failed jobs store a category, never exception text

`AiJob.failed()` takes a `JobFailure`, a closed set of user-facing messages: provider unavailable, unusable model output, unreadable document, no readable pages, quota exceeded, internal error. The worker classifies the exception, logs the detail server-side (a warning for expected failures, the traceback for anything else) and stores only the message. `JobOut` reads `AiJob.public_error`, which shows any stored text outside that set, such as rows written before this change, as the internal-error message.

### Interrupted jobs are requeued

Requeue was chosen over marking the jobs failed: the owner gets their cards without acting, and nothing is charged twice.

- **At startup**, before the worker starts, `GenerationRequestService.recover_interrupted()` runs in its own unit of work. QUEUED jobs are enqueued again. RUNNING jobs are recovered with the conditional `try_requeue_interrupted(job_id, attempt)` (one `UPDATE` in SQL) and enqueued after the commit.
- **Recovery spends an attempt.** With attempts left the job is QUEUED as the next attempt; once they are spent it is FAILED with the internal-error message. A job that crashes the process every time it runs (a hostile PDF, an out-of-memory kill) therefore stops after `max_attempts` runs instead of crash-looping the instance.
- The job resumes from `completed_units`: finished pages are neither regenerated nor charged again. A job that finished in the meantime no longer matches the conditional update and is left alone.
- **On shutdown**, stopping the worker cancels the running job, which rolls back its in-flight page and is handed back through the same conditional recovery before the cancellation continues.
- **While running**, a sweep every minute re-offers QUEUED jobs idle for a minute and recovers RUNNING jobs not updated for 45 minutes. The worker updates a job with every page, and one provider call with SDK retries can take many minutes, so 45 minutes idle means its runner is gone. This picks up the job an old instance hands back during an overlapping deploy. `InProcessJobQueue.enqueue` is idempotent, so the sweep never queues a waiting job twice.
- **The attempt is a fence.** An old process may still be running the job, for example during an overlapping deploy. The worker re-reads the job before each page, before recording progress and before finishing. When the job is no longer RUNNING at the run's own attempt, the worker rolls back the in-flight page and stops without writing. A superseded run that then fails does not mark the new run FAILED.
- `SqlAiJobRepository.get` reads with `populate_existing`. The worker's session lives across commits with `expire_on_commit=False`, so its re-reads used to return the cached row and missed a cancel or a requeue committed by another session.
- A recovery error is logged and does not stop the API from starting; the jobs are picked up at the next start.

### `FLASHCARDS_MAX_UPLOAD_PAGES` is the page cap

The setting (default 20, at least 1; 300 since ADR 0022, which also lifts it for admins) reaches the request-time check, the worker's extractor cap and document-chat indexing through `Repositories.max_upload_pages`, so the three always agree for a given role.

## Consequences

- Sign-ins and uploads beyond the limiter sizes wait instead of running at once. With 4 Argon2 slots, a sign-in under load can take a few hundred milliseconds longer; requests that do not hash or parse are unaffected.
- Every non-browser client must send `X-Requested-With` (any value) or a trusted `Origin` on unsafe requests. Requests from an origin other than the public one and the API's own host, such as a second web front end, are refused unless `FLASHCARDS_PUBLIC_BASE_URL` is changed.
- A crash or deploy costs at most the page that was in flight for each running job, and one of the job's attempts. During an overlapping deploy the new instance's startup recovery also interrupts jobs the old instance is still running; the fence stops the old run, at the cost of that page.
- Between the old run's last re-read and its commit there is a window of milliseconds in which both runs could record the same page; closing it would need a conditional progress update.
- One slow PDF holds the single PyMuPDF slot until it finishes; other PDF work waits behind it.
- Remaining work, tracked in the threat model: sandboxed parsing with time limits, a per-IP rate limit, and JSON field-size caps.
