# ADR 0010: Account security, user management and the audit log

- **Status:** Accepted
- **Date:** 2026-11-08

## Context

Several P0 requirements cover account security and administration:

- F-AUTH-2: email verification and password reset
- F-AUTH-4 and F-ADMIN-2: admins suspend, change the role of, and delete users, with impersonation disabled
- F-AUTH-5 and F-ADMIN-5: an immutable audit log of admin and security events
- F-AUTH-6: lockout on repeated failed sign-ins

These flows send email and change who can act on the instance. Mistakes in them are security bugs rather than inconveniences.

## Decision

### Request transactions and email

- Every route declares its unit of work with `Depends(..., scope="function")`. The transaction commits, or rolls back on an error, when the handler returns and before the response is sent.
- Emails go out after the response through a sender that logs and swallows failures. So the token in an email is always committed before the email can be opened, a mail outage never fails or undoes a request, and the SMTP exchange never holds a database connection.
- SMTP always uses a verified TLS connection (`ssl.create_default_context()`). `FLASHCARDS_SMTP_SECURITY` selects:
  - `starttls`, usually on port 587
  - `ssl`, implicit TLS, usually on port 465
  - `none`, for a local relay only, with credentials refused
- Email addresses must be plain ASCII addresses within the RFC 5321 length limits. Commas, spaces, quotes, angle brackets and control characters are refused, so an address cannot add a recipient or a header.

### Verification and reset tokens

- Tokens are 32 random bytes, stored only as SHA-256 hashes and used once. Use is a conditional `UPDATE ... WHERE used_at IS NULL`.
- Lifetimes: verification links last 48 hours, reset links one hour. Issuing a new token of a purpose voids the older ones.
- A new email of either kind is not sent within one minute of the previous one.
- Links carry the token in the URL fragment (`#token=`). Browsers never send the fragment to a server, so tokens stay out of access logs and Referer headers.
- A reset request answers 202 whether or not the account exists.
- A completed reset signs the user out everywhere and clears any sign-in lockout.
- Suspending a user voids their unused links.

### Sign-in lockout

- Attempts are counted per email (keyed by a hash of the normalized address, unknown emails included), before the password is checked. Counting is one atomic statement: `INSERT ... ON CONFLICT DO UPDATE` in Postgres.
- At most five attempts reach the password check in a 15-minute window, however many arrive at once. The fifth starts a 15-minute lock. Later attempts are refused with 429 and a `Retry-After` header, and they never extend or end the lock.
- A successful sign-in clears the count. Only the start of a lock is audited, so guessing cannot flood the log.
- Because the key is an email, anyone can keep an account locked. An admin can clear a lockout with `POST /admin/users/{id}/unlock`, which is audited.

### User management

- Admins can list, suspend, unsuspend, change the role of, and delete users. They can never do this to their own account. There is no impersonation.
- Suspension signs the user out everywhere.
- Every admin change first locks the active admin rows (`SELECT ... FOR UPDATE`, in id order), then the target's row. Concurrent changes therefore apply one after the other without deadlocking. An admin who lost the role while their request was waiting is refused (403).
- Together with the self-action rule, this keeps at least one active admin. Account deletion takes the same lock and refuses the last active admin (409).
- When an admin is suspended, demoted or deleted, the invites they sent and that are still unredeemed are revoked and audited.
- Flows that change a user (reset, verification) lock the user's row before writing, so a concurrent suspension or role change is never overwritten.

### Audit log

- The audit log records:
  - invite creation and revocation
  - suspension, unsuspension, role changes, unlocks and deletions
  - self-service account deletion
  - lock starts
  - reset requests and completions
  - email verification
- It is append-only: the application has no update or delete path for it, and it is kept when the accounts it mentions are deleted.
- `GET /admin/audit` returns events newest first. The cursor is the id of the last event of the previous page. Events carry a database-assigned sequence, so pages never skip or repeat an event, even when timestamps tie.

## Consequences

- A slow or failing mail server delays or drops emails but never affects requests. Lost emails are visible only in logs. Users can request another email after the cooldown.
- Admin changes are serialized instance-wide. They are rare, so the cost is negligible.
- The lockout is keyed by email only. It slows guessing against one account but not a single client spraying many accounts. An IP- or device-keyed limit belongs at the reverse proxy, or in a later change.
- Rows in `login_throttle`, `user_tokens` and `audit_events` are not purged yet. Expired throttle rows and used or expired tokens are safe to delete, and a periodic sweep is planned. Audit retention will be a configurable policy.
- A reset request for an existing account does slightly more work than one for an unknown email. The difference is small, but it is not constant-time.
