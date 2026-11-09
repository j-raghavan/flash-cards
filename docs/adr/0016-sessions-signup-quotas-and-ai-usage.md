# ADR 0016: Device list, public sign-up, invite resend, AI quotas and usage

- **Status:** Accepted
- **Date:** 2026-11-08

## Context

ADR 0010 covered most account and admin requirements. These remained open:

- F-AUTH-2: session management with a device list and revoke
- F-AUTH-7: an `INVITE_ONLY` setting, true by default, that self-hosters can turn off
- F-ADMIN-1: resending an invite
- F-ADMIN-3 and F-AI-11: a monthly AI page quota per user, with a global default and per-user overrides, and a per-instance spend cap
- F-ADMIN-4 (P1): usage and cost by user and provider
- F-LEGAL-2: an AI data-use disclosure that names the provider and links its terms

Providers already returned token counts with every response, but nothing recorded them.

## Decision

### Device list (F-AUTH-2)

- A session stores a short device label such as "Firefox on macOS", derived from the User-Agent at sign-in. Labels come from a fixed list of browsers and systems, so the raw User-Agent, which can carry arbitrary text and fingerprinting detail, is never stored or shown.
- A session records when it was last seen. A request writes this only when the stored value is at least five minutes old, so an active session is written at most once per interval.
- Last-seen lives in its own table, `session_activity`, not on the session row. The write uses `SELECT ... FOR UPDATE SKIP LOCKED`, so:
  - a request never waits for another request of the same session to finish
  - revoking a session never waits for a long request of that session, such as an AI call, because the session row is never locked by activity
- `GET /auth/sessions` lists only live sessions: created, last seen, device and whether it is the current one. Session tokens and hashes are never returned.
- `DELETE /auth/sessions/{id}` revokes one of the caller's live sessions. `POST /auth/sessions/revoke-others` revokes all but the current one, counting only live sessions. Both are audited.
- Deleting sessions (account deletion) removes their activity rows too, so it can wait for a request that is recording activity at that moment.

### Public sign-up (F-AUTH-7)

- `FLASHCARDS_INVITE_ONLY` defaults to true. `GET /auth/options` tells a client whether sign-up needs an invite.
- With it set to false, `POST /auth/register` creates a member account. With it true, that route answers 404, as if it did not exist. Invite sign-up keeps working either way.
- Protections:
  - Every attempt is counted per client address before anything else, using the sign-in throttle's window and limit (five attempts per 15 minutes, ADR 0010). Refusals are returned rather than raised, so the count is committed.
  - The answer is always 202, whether or not the email has an account. A new account gets a verification email. An existing account gets a notice that someone tried to sign up, with a link to reset the password. The password is hashed in both cases, so the work matches.
  - The route never signs the caller in. While sign-up is public, sign-in requires a confirmed email address, so nobody can use an account made with someone else's or a throwaway address. Signing up again with an unconfirmed address sends a fresh confirmation link (within the one-minute cooldown) instead of the notice, which is how an owner who lost the first link gets another.
  - Email and password validation are the same as for invites. Every new account is audited (`user.registered`).
- The client address is the connection's peer as uvicorn reports it. uvicorn reads `X-Forwarded-For` only from the addresses in `FORWARDED_ALLOW_IPS` (default `127.0.0.1,::1`). An operator whose reverse proxy runs elsewhere, such as in another container, must set it to the proxy's address; otherwise every client shares the proxy's address and the limit applies to the whole instance.

### Invite resend (F-ADMIN-1)

- An invite addressed to an email is now emailed when it is created. Its link carries the token in the URL fragment, like the other account links.
- `POST /admin/invites/{id}/resend` sends it again with a **new token**. Only the token's hash is stored, so the old link cannot be repeated; rotating also means a link that leaked earlier stops working. The rotation is one conditional `UPDATE` on an unredeemed, unrevoked, unexpired invite, so a resend can never revive a spent invite.
- The expiry is unchanged. A link-only invite has no address and answers 409. Resends are audited (`invite.resent`).
- Invite email addresses are now validated like account emails, since the server sends mail to them.

### Quotas and the spend cap (F-ADMIN-3, F-AI-11)

- Admins set the instance's default monthly page limit and monthly spend cap (`GET`/`PUT /admin/quotas`), and per-user page limits that override the default (`GET`/`PUT /admin/users/{id}/quota`, where null clears the override). Until an admin sets them, the defaults come from `FLASHCARDS_AI_DEFAULT_MONTHLY_PAGES` and `FLASHCARDS_AI_MONTHLY_SPEND_CAP_USD`. Every change is audited with its old and new values.
- Users see their own limit and use at `GET /account/ai-quota`.
- Generation reserves pages against the user's limit in the same atomic statement as before, with the limit passed in.
- Before any new AI work, the month's recorded spend is compared with the cap, and the work is refused with 429 once it is reached. New AI work means a synchronous call (grading, summaries, mock exams, chat), a new generation job, and a retry. The check runs before the daily call count and before page reservation, so refused work uses neither.
- A call's cost is known only after it returns, so the cap cannot be reserved in advance. Work already running when the cap is reached finishes, so the cap can be overshot by the work in flight at that moment. A running generation job is not stopped between pages.

### Usage and cost (F-ADMIN-4)

- Every model call is recorded in `ai_usage`: user, provider, model, feature, input and output tokens, and cost in integer micro-USD.
- Cost uses the operator's prices, `FLASHCARDS_AI_INPUT_USD_PER_MTOK` and `FLASHCARDS_AI_OUTPUT_USD_PER_MTOK`. They default to zero, which records tokens but no cost; a spend cap needs prices to have any effect.
- The generation worker records and commits each page's usage as soon as the model answers, so a page whose output is unusable is still counted.
- A synchronous call's usage is recorded and committed together with its daily-call charge as soon as the model answers, before its output is parsed. Output that fails validation still counts, so a user cannot steer the model into unusable answers to get calls that never count. Mock exam grading records the submission before the call, so it reopens the exam before committing, and the user can submit again.
- A call that the provider itself refuses or fails (`LlmError`), including the provider's own output validation, is not recorded, even if the provider billed it.
- `GET /admin/usage?period=YYYY-MM` returns the month's total, the cap, and totals by user and by provider and model. The default is the current month. Each user's row also carries `pages`: the generation pages reserved against their monthly quota in that month, the same count the quota is enforced on; a user with reserved pages but no recorded call yet still gets a row. Page counters are erased with the account while usage rows are kept, so a deleted account's row shows its calls and cost with 0 pages.
- Usage rows hold a random user id and counts, never content or contact details, and are kept when an account is deleted, for cost accounting, as the audit log keeps ids (ADR 0010). Once the account is gone the id identifies nobody. Per-user overrides are deleted with the account.

### AI data-use disclosure (F-LEGAL-2)

- `GET /legal/ai-disclosure` is public, so a client can show it before sign-up and next to AI features.
- It states that Flash-Cards never uses user content to train models, explains what is sent to the provider and why, and names the configured provider with a link to its terms.
- Anthropic has a default terms link. For any provider, `FLASHCARDS_AI_PROVIDER_NAME` and `FLASHCARDS_AI_PROVIDER_TERMS_URL` override the name and link; a self-hosted endpoint should set both.
- With no provider configured, it says that AI is off and nothing is sent.

## Consequences

- Public sign-up is limited per client address, not per account. Spreading attempts over many addresses gets past it, so an operator who opens sign-up should also limit requests at the reverse proxy.
- Two concurrent public sign-ups for the same new email conflict on the unique email. The loser's transaction rolls back and the request fails, so no duplicate account is created.
- The spend cap is only as accurate as the configured prices and can be overshot by work already in flight.
- Recording last-seen is best effort: a request that fails rolls its write back, and a request skips the write while another holds the row.
- `ai_usage`, `session_activity` rows of revoked sessions, and audit events are not purged yet, as noted in ADR 0010.
