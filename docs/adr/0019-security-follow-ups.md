# ADR 0019: Security follow-ups: link expiry, rate limits, sign-up race, spend holds

- **Status:** Accepted
- **Date:** 2026-11-09

## Context

The threat model (`docs/security/threat-model.md`) and ADRs 0009, 0016 and 0017 left these open:

1. Share links never expired (ADR 0009).
2. Nothing limited requests per client address: sign-in was throttled per email, public sign-up per address, and nothing else (ADR 0010, 0016).
3. Two concurrent sign-ups with the same new email could answer 500 instead of the usual answer (ADR 0016, Consequences).
4. The monthly spend cap was checked before work started but could not reserve spend, so calls in flight could overshoot it by any amount (ADR 0016).
5. Deleting an account cleared the session cookie only; anything the browser had cached stayed.

## Decision

### Share links expire

- A new link stops working after 1, 7, 30 or 90 days, or never; the owner chooses at creation, and the default is 30 days. `POST /decks/{id}/shares` takes `expires_in_days` (omitted: 30; `null`: never; any other value: 422).
- `share_links.expires_at` is nullable. Migration 0018 adds it without a default, so links made earlier keep working until revoked.
- Preview and clone answer 404 from the moment a link expires, the same as for an unknown or revoked token.
- The link list returns `expires_at`. `PATCH /shares/{id}` can set a new lifetime, counted from now (`expires_in_days`, `null` for never), together with or instead of `allow_clone`. An expired link, like a revoked one, cannot be changed (404): the owner creates a new one.
- The web share page has a lifetime picker and shows each link as active until a date, never expiring, expired or revoked.

### Per-address rate limit

- `api/rate_limit.RateLimitMiddleware`, a pure ASGI middleware with no dependencies, keeps a token bucket per client address for two rules:
  - every POST under `/auth/` (sign-in and its second step, invite and public sign-up, password reset, email verification and its resend, and two-factor management): 20 a minute;
  - every request under `/shared/` (preview and clone): 60 a minute.
- A bucket holds a minute's allowance and refills continuously. An empty bucket answers 429 `{"detail": "Too many requests. Try again later."}` with `Retry-After` in whole seconds, rounded up. It runs before the CSRF check and the routes, so a refused request costs no database work and no Argon2.
- **Client address.** The connection's peer as the ASGI server reports it. uvicorn rewrites it from `X-Forwarded-For` only for proxies in `FORWARDED_ALLOW_IPS` (ADR 0016). For deployments where uvicorn is not told to trust the proxy, `FLASHCARDS_TRUSTED_PROXY_COUNT` (default 0) names how many proxies append to `X-Forwarded-For`; the address is then that many entries from the right, the one the outermost trusted proxy saw. With 0 the header is ignored, so a client cannot choose its own bucket by sending it. A request with fewer entries than trusted proxies falls back to the peer.
- IPv6 addresses are keyed by their /64 network, since a single host commonly controls a whole /64; IPv4-mapped IPv6 addresses count as IPv4. A key that is not an address is cut to 64 characters.
- **Bounded memory.** Each rule keeps at most 10,000 buckets in an LRU order and forgets the least recently used one when full. A forgotten bucket starts full again, which only matters to an attacker who already controls more than 10,000 addresses (or /64 networks) and so gets past any per-address limit.
- **Per process.** The counts live in memory: each API process limits on its own, and a restart resets them. The runbook shows a proxy limit (nginx `limit_req`, or Caddy with the `caddy-ratelimit` module) for a global limit.
- `FLASHCARDS_RATE_LIMIT_ENABLED` (default true), `FLASHCARDS_RATE_LIMIT_AUTH_PER_MINUTE` and `FLASHCARDS_RATE_LIMIT_SHARED_PER_MINUTE` configure it. The API test suite and the end-to-end suite turn it off, because every test client shares one address; `tests/test_rate_limit.py` turns it on.
- The existing per-email sign-in lockout and per-address public sign-up throttle stay: they are stored in the database, so they hold across processes and restarts.

### Concurrent sign-ups with one email

- `SqlUserRepository.add` inserts inside a savepoint. When a concurrent sign-up commits the same email first, the unique index fails only the savepoint, `EmailAlreadyRegistered` is raised, and the request's transaction stays usable.
- Public sign-up treats that as an existing account: it reads the winner and answers 202 with the same body as always, sending the "account exists" notice or a fresh confirmation link to the owner. Its throttle attempt still commits, and nothing is audited for the losing request. Neither the answer nor its timing tells the caller there was a race.
- Invite sign-up keeps answering 409, as for any existing email; its transaction rolls back, so the invite is not consumed.

### Spend holds

- Before every model call (grading, summaries, mock exams, document chat, and each page of a generation job), an estimate of its cost is held against the monthly cap in `ai_spend_holds`. The hold succeeds only if the month's recorded cost plus its live holds is under the cap; otherwise the call is refused with the existing `SpendCapReached` (429 for synchronous calls; a generation job fails with the quota message and keeps the pages it finished, so a retry next month resumes).
- **Atomic.** `SqlSpendHoldRepository.try_hold` runs in its own short transaction, under `pg_advisory_xact_lock` for the billing month. Under READ COMMITTED each statement after the lock sees everything the previous lock holder committed, so concurrent holds are serialized and each sees the others. The transaction commits at once: no lock or row is held while the model runs, and other requests see the hold immediately.
- **Settled with the usage.** Recording the call's usage deletes its hold in the caller's transaction, which commits right away (ADR 0016), so the period's total never counts the call twice or not at all. A call that fails at the provider, is refused by the daily allowance, or is cancelled releases its hold in its own transaction. A hold nobody settles (the process died mid-call) lapses after an hour and is purged by the next hold.
- **Estimate.** Output is priced at the request's `max_output_tokens`, which the provider never exceeds. Text input is priced at one token per UTF-8 byte of the instructions, schema and text documents, more than any tokenizer produces. Each image or other non-text document is priced at 2,000 input tokens; providers bill a downscaled image at about 1,600, but this is an estimate, not a bound.
- **The bound.** Holds are taken while recorded cost plus live holds is under the cap, so the committed total can pass the cap by less than one call's estimate, plus whatever running image calls cost beyond their estimate. Without prices configured, estimates and costs are zero, as before.
- Starting a generation job or a retry still checks the cap first (now counting live holds), so refused work never queues. The in-memory mode keeps holds in the process, over the same usage records.

### Clear-Site-Data on account deletion

- `DELETE /account` answers with `Clear-Site-Data: "cache"`, so the browser drops its HTTP cache for the origin.
- `"storage"` was left out. It would also erase IndexedDB and local storage for every account on the device, including another person's answers still queued for sync on a shared device, and unregister the service worker that makes the app work offline. The web client already removes the deleted account's own queued answers, notice flag, remembered user and Cache Storage entries (`AccountPage.tsx`, `AuthContext.tsx`) and then signs out locally; sign-out never depended on storage being cleared by the server.

## Consequences

- Shared links stop working after 30 days unless the owner picks a longer lifetime or none; recipients then need a new link.
- One address can make 20 sign-in-related POSTs a minute per API process. A sign-in with a second factor uses two. Many people behind one NAT share that allowance; an operator can raise it.
- A burst of AI calls close to the cap is refused earlier than before, because estimates are high; settled calls give the difference back at once.
- A crashed process can block up to one estimate per in-flight call for an hour.
- Spend holds use a Postgres advisory lock, so the SQL adapter stays Postgres-only, as it already was.
