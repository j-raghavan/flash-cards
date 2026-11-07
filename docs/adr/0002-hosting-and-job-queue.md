# ADR 0002: Hosting and job queue

- **Status:** Superseded by [ADR 0004](0004-phased-hosting.md)
- **Date:** 2026-11-07

## Context

The hosted instance is invite-only (roughly 10–20 users) and maintained by one person. The app needs a web API, a long-running worker for AI generation (tens of seconds to minutes), Postgres with pgvector, and object storage. The same container images must run for self-hosters.

Options considered (prices checked November 2026, see the PRD for sources):

| Option | Est. monthly cost | Outcome |
|---|---|---|
| Fly.io + Fly Managed Postgres | ~$47–48 | Rejected: the $38 database floor dominates the bill |
| Vercel Pro + Neon | ~$20–35 | Rejected: needs a different job path (Queues is in beta) and breaks image parity with self-hosting |
| Single VPS (Docker Compose) | ~€6–12 | Fallback: cheapest, but we would own patching, backups and failover |
| Railway or Render with bundled Postgres | ~$26 / ~$40–62 (single third-party estimate) | Not chosen: not verified in depth; no clear advantage over Fly + Neon |
| **Fly.io machines + Neon Postgres + Tigris** | **~$8–25** | **Chosen** |

## Decision

- API and worker run as separate Fly.io process groups from one image.
- Postgres is Neon (managed backups and point-in-time restore); files are on Tigris (S3-compatible).
- Background jobs use a Postgres-backed queue behind a `JobQueue` port, so Redis is not a dependency.
- Every infrastructure dependency stays behind a port. The same code runs on Docker Compose with plain Postgres and MinIO.

## Known risk and the M0 spike

A worker that polls Postgres can keep Neon's compute awake and defeat scale-to-zero, which could cost around $19/month instead of ~$0–5 (our estimate, assuming a 0.25 CU minimum; not verified). Before building on this, M0 must measure:

1. Does the chosen queue (LISTEN/NOTIFY or polling) keep Neon awake?
2. If so, use an on-demand worker: the API enqueues a job and starts the stopped Fly machine, which drains the queue and exits.

**Fallback:** Postgres on a Fly machine with a volume, snapshots and nightly `pg_dump` to Tigris (~$17/month total).

## Consequences

- Lowest operations burden for a solo maintainer; the riskiest data (Postgres) is managed.
- Costs stay near $10–25/month plus AI usage.
- The queue-awake-Neon behaviour is unverified until the spike.
