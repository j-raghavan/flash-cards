# ADR 0021: End-to-end tests and a test-only fake AI provider

- **Status:** Accepted
- **Date:** 2026-11-08

## Context

PRD section 8 requires end-to-end Playwright suites for invite, upload, review and share. Those journeys run through AI card generation, which needs a model. A real provider makes the suite slow, costly, non-deterministic and dependent on a secret, and the refusing `NullLLMProvider` fails every generation job.

The suite also needs an admin to create invites and a way to read the emails the app sends (password reset), without adding endpoints that exist only for tests.

## Decision

### The suite

- The suite lives in `e2e/` at the repository root, with its own `package.json`. It drives both apps, so it belongs to neither; keeping it out of `apps/web` leaves that package's lint, type check and coverage gates unchanged.
- Playwright starts the real API (uvicorn, in-memory mode) and the real production web build (`vite preview`, whose `/api` proxy forwards to the API). Chromium runs by default; Firefox and WebKit run with `E2E_BROWSERS`.
- The first admin signs up with the invite that `FLASHCARDS_DEV_BOOTSTRAP_INVITE` logs at startup. Tests create their own invites through `POST /admin/invites`.
- Emails are read from the API log: in development `LogMailer` logs each message in full. The suite points uvicorn's logging at a file it reads.
- Every test signs up its own users, so tests share no data and run in parallel.
- CI runs the suite in an `e2e` job, with one retry; tests that pass only on retry are listed in the job summary.

### The fake AI provider

`FLASHCARDS_LLM_PROVIDER=fake` selects `FakeLLMProvider`. It answers card generation with two fixed drafts per page (one Basic, one Cloze) after one second, so a multi-page job's live progress can be observed. It refuses every other AI request, as when no provider is configured.

It can only run where it cannot matter:

- `Settings` refuses `llm_provider=fake` at startup unless `FLASHCARDS_ENVIRONMENT` is exactly `development` or `test`. Any other value, including `production` (the Docker image's default) and values such as `staging`, is refused. This is an allowlist, so a new environment name is refused until it is added.
- `Settings` also refuses it when `FLASHCARDS_DATABASE_URL` is set. A real deployment persists data in SQL; the fake runs only in in-memory mode, whose data is lost on restart.
- `create_app` checks both rules again before wiring the provider, so a `Settings` object built without validation cannot enable it either.
- It logs a warning at startup naming itself.

Unit tests cover each refusal, including the variable set from the environment in production.

## Consequences

- The PRD's end-to-end journeys run on every pull request in a few minutes, with no AI secret and no network access beyond the runner.
- A self-hoster who sets `FLASHCARDS_LLM_PROVIDER=fake` on a production instance gets a startup error rather than an instance that produces fixed cards.
- The suite depends on the development log format of `LogMailer` and the bootstrap invite. A change to either message must update `e2e/support/`.
- Other AI features (grading, summaries, mock exams, chat) are not covered end to end, because the fake refuses them.
