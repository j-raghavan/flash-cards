# ADR 0004: Phased hosting, from free tiers to a paid stack

- **Status:** Accepted (supersedes [ADR 0002](0002-hosting-and-job-queue.md))
- **Date:** 2026-11-07

## Context

The project is free and non-commercial at first, with a possible commercial option later. Hosting should cost close to nothing while usage is small, and moving to a paid stack must not require a rewrite. Prices and limits below were checked in November 2026 and change often; verify before relying on them.

## Decision

| Phase | Audience | Compute | Data | Est. cost |
|---|---|---|---|---|
| **0** | Maintainer and a few invited testers | Render free web service (API serves the built frontend; jobs run in-process) | Supabase Free (Postgres, Storage) | ~$0 + pennies of AI |
| **1** | ~20–100 invited users | Fly.io machines (API + worker) | Supabase Pro (Postgres, Storage, pgmq queue) | ~$32/month + AI |
| **2** | Public and commercial | Same as Phase 1, scaled; billing added | Same, larger plans | Usage-based |

Rules that keep the phases interchangeable:

- Every infrastructure dependency sits behind a port: `JobQueue`, `ObjectStore`, `LLMProvider`, `Mailer`.
- Phase 0 uses an `InProcessJobQueue` adapter. Phase 1 adds a `PgmqJobQueue` adapter.
- Supabase is used only as Postgres, S3-style storage and queue. The FastAPI backend keeps its own auth, sessions and authorization; the browser never talks to Supabase directly.
- Schema changes go through Alembic, so any plain Postgres works for self-hosters.

## Phase 0 constraints and how we handle them

- **Free web services sleep** after about 15 minutes idle, with cold starts of 30–60 seconds. Accepted for testers. The UI shows a "waking up" state.
- **Small instance** (512 MB RAM, 0.1 CPU). Cap uploads (about 20 pages per file) and process pages in a streaming fashion. (ADR 0022 raised the cap to 300 pages once extraction streamed one page at a time.)
- **Jobs in the API process die on sleep or restart.** Job state lives in Postgres; jobs are idempotent and resumable; at boot the app requeues jobs stuck in `running`.
- **Supabase Free pauses after a week of inactivity.** We rely on normal use and do not add keep-alive tricks. If it pauses, an admin resumes it.
- **Connection strings:** use Supabase's pooled connection string; to be verified, since direct connections may be IPv6-only.
- **Custom domain** on Render's free tier is unverified; the default Render URL is acceptable for Phase 0.

## Triggers to move to Phase 1

Any of: regular cold-start complaints, jobs failing from memory limits, more than about 20 active users, or the Supabase free database nearing 500 MB.

## Consequences

- Near-zero cost while validating the product.
- A small amount of extra code (two queue adapters) buys a no-rewrite migration.
- Phase 0 is not robust: it is for testers, not for people who depend on it.
