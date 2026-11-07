# ADR 0005: Async SQLAlchemy persistence with a per-request unit of work

- **Status:** Accepted
- **Date:** 2026-11-07

## Context

M0 started with synchronous in-memory adapters behind the repository ports. Adding Postgres forced a choice between synchronous and asynchronous SQLAlchemy, and a decision about transaction boundaries. Two requirements shaped it:

- The PRD stack (Section 6.2) commits to FastAPI with async SQLAlchemy 2, and later milestones lean on async for SSE generation progress and streaming.
- Invite-only signup must consume the invite and create the user atomically (F-AUTH-1): either both happen or neither does, even under a race.

## Decision

**Go async now.** The repository ports, the invite/registration/auth services, and the API routes are `async`. `Clock` and `PasswordHasher` stay synchronous because they are pure CPU. This was done while the surface was small (three services), which is the cheapest time to pay for it.

**One unit of work per request.** `config.database_url` selects the mode:

- Unset: in-memory adapters, one shared `Container` (dev and tests).
- Set: a `Container` is built per request, with the three SQL repositories bound to a single `AsyncSession` opened inside `session.begin()`. The transaction commits after the response and rolls back if the handler raises.

Because all three repositories in a request share that session and transaction, `RegistrationService.register` consumes the invite (a conditional `UPDATE ... WHERE redeemed_at IS NULL`) and inserts the user in the same transaction. A unique-email collision raises after the redemption, the transaction rolls back, and the invite stays active. The composition root (`api/provider.py`) is the only place that knows which mode is active; services and routes are unchanged between modes.

**Timing side-channel.** `AuthService` verifies a precomputed dummy Argon2 hash when no user matches, so a missing account and a wrong password cost the same. The dummy hash is computed once at startup and injected, so per-request service construction in SQL mode does no Argon2 work.

## Consequences

- Matches the PRD stack and avoids a larger async migration later.
- Atomic registration without a heavyweight unit-of-work abstraction in the domain: the transaction boundary lives at the request edge.
- Integration tests run against a real Postgres via Testcontainers (Docker required for the suite); the in-memory mode keeps the unit tests fast and dependency-free.
- Schema changes go through Alembic (see the migrations), so any plain Postgres works for self-hosters, consistent with [ADR 0004](0004-phased-hosting.md).
