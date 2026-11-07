# Product Requirements Document: Flash-Cards

| | |
|---|---|
| **Status** | Draft v0.1 |
| **Date** | 2026-11-07 |
| **Owner** | Jay Raghavan |
| **License** | AGPL-3.0-only with a CLA (see ADR 0003) |
| **Distribution** | Public repo; invite-only hosted instance |

## 1. Summary

Flash-Cards is an open-source, AI-assisted spaced-repetition study platform. A learner uploads lecture slides, PDFs, photos of handwritten notes or a YouTube link; the system generates reviewable flashcards, schedules reviews against an exam date and target grade, and tracks proficiency. It is functionally equivalent to the commercial product *Retain Cards* (retain.cards), built from public information only, with original code, branding and assets.

The hosted instance is **invite-only**. Anyone can self-host the open-source code.

### 1.1 Goals

1. Feature parity with every publicly documented Retain capability (Section 4).
2. Professional engineering quality: tested, typed, observable, secure, documented, reproducibly deployable.
3. Invite-only access with an admin console.
4. Provider-agnostic AI so the product runs on a hosted API or a local model.
5. A clean, intuitive, accessible UI that works on desktop, tablet and phone (PWA).

### 1.2 Non-goals (v1)

- Native iOS/Android apps (PWA instead; native is a later phase).
- Payments, subscriptions and paywalls. Quotas replace them.
- B2B licensing, class/school management, creator-affiliate programs.
- Reuse of Retain's name, logo, copy, imagery or UI assets.

### 1.3 Evidence base and limits

Everything below is derived from the public marketing site, public blog comparisons, the App Store and Play listings, third-party UX walkthroughs and reviews. The logged-in web app (`app.retain.cards`) was **not** inspected. Behaviors marked *(inferred)* are reasonable design choices where the public material is silent, not confirmed facts. Retain's claims ("20% more efficient than Anki", "220 million learning records") are marketing and are not reproduced as our claims.

## 2. Users and use cases

| Persona | Need |
|---|---|
| **Exam student** | Turn lecture PDFs into cards, get a daily plan that ends at the exam date, see predicted readiness. |
| **Anki migrant** | Import existing `.apkg` decks without losing scheduling history or media. |
| **Self-learner** | Create and review cards on any topic with no exam date. |
| **Instance admin** (Jay) | Invite users, cap AI usage, see health and cost. |

Primary flow: **upload → verify cards → set exam date and grade → follow daily plan → review → track proficiency**.

## 3. Feature inventory from the reference product

| # | Reference capability | Source | Our requirement |
|---|---|---|---|
| R1 | AI flashcards from PDF, PNG, JPEG (text, math formulas, handwriting) | Site, stores | F-AI-1 |
| R2 | AI flashcards from YouTube links and online lectures | Stores, reviews | F-AI-2 |
| R3 | Three thoroughness levels for PDF generation | Site | F-AI-3 |
| R4 | Review and edit AI cards before use ("Co-Pilot Mode") | Site, blog | F-AI-4 |
| R5 | Source reference on every AI card | Blog | F-AI-5 |
| R6 | Card types: Q/A, Cloze, Image Occlusion, custom Anki-style note types | Site | F-CARD-1..4 |
| R7 | Anki deck import (`.apkg`), progress sync across devices | Site, stores, blog | F-IMP-1..3 |
| R8 | Adaptive spaced repetition (vs Anki SM-2) | Site | F-SRS-1 |
| R9 | Exam date + desired grade → personalized learning plan | Site, stores | F-PLAN-1..3 |
| R10 | Predicted knowledge level on exam day | Blog | F-PLAN-4 |
| R11 | Daily goals, progress tracking, confetti on goal completion | Stores, UX review | F-PROG-1..3 |
| R12 | Per-deck proficiency percentage and progress ring | Site | F-PROG-2 |
| R13 | Study modes: AI-evaluated typed answer, self-evaluated confidence | UX review | F-STUDY-1..2 |
| R14 | Voice mode for study sessions | Play listing (v2.1.1) | F-STUDY-4 |
| R15 | Deck management: filter by state, sort by proficiency, bulk move/delete | UX review | F-DECK-1..3 |
| R16 | Deck sharing by link; revoke link; allow/deny cloning | Site | F-SHARE-1..3 |
| R17 | Deck statistics (total, new cards) | Site | F-PROG-4 |
| R18 | AI summaries (unlock at ≥20 cards) | UX review, store | F-AI-6 |
| R19 | Mock exams (unlock at ≥20 cards), timed | UX review, store | F-AI-7 |
| R20 | AI chat over uploaded documents (Q&A, summaries) | UX review | F-AI-8 |
| R21 | AI-generated podcasts from deck content | Store changelog | F-AI-9 (P2) |
| R22 | Free tier with monthly AI-page quota (10 pages/month) | Blog | F-ADMIN-3 (configurable) |
| R23 | 10 UI languages (en, de, fr, pl, es, pt, it, ja, tr, hi) | Site | NF-I18N-1 |
| R24 | Web, iOS, Android, iPad | Site, stores | PWA (v1) |
| R25 | Onboarding wizard (upload → verify → date/grade → plan) | Stores, UX review | F-ONB-1 |
| R26 | Minimalist, Notion-style UI | Blog | NF-UX-1 |
| R27 | Imprint, privacy policy, terms | Site | F-LEGAL-1 |
| R28 | Account email/password sign-in, password reset | UX review | F-AUTH-1..2 |

Deliberately excluded: hard paywall with trial, rating prompt during onboarding, creator program, B2B licensing page.

## 4. Functional requirements

Priority: **P0** = launch blocker, **P1** = launch target, **P2** = post-launch.

### 4.1 Access and accounts (invite-only)

| ID | Requirement | Pri |
|---|---|---|
| F-AUTH-1 | Sign-up is possible only with a valid, unexpired, single-use invite token. No public registration endpoint. | P0 |
| F-AUTH-2 | Email + password (Argon2id), email verification, password reset, session management with device list and revoke. | P0 |
| F-AUTH-3 | Optional passkeys (WebAuthn) and TOTP 2FA. | P1 |
| F-AUTH-4 | Roles: `admin`, `member`. Admin can invite, suspend, delete users, set quotas. | P0 |
| F-AUTH-5 | Invites: created by admin by email or as a link; expiry (default 7 days); revocable; optional pre-assigned role and quota; audit-logged. | P0 |
| F-AUTH-6 | Rate limiting and lockout on auth endpoints. | P0 |
| F-AUTH-7 | Instance setting `INVITE_ONLY=true` (default). Self-hosters may set `false`. | P1 |

### 4.2 Content ingestion and AI generation

| ID | Requirement | Pri |
|---|---|---|
| F-AI-1 | Upload PDF, PNG, JPEG; extract text, formulas (LaTeX) and handwriting; generate cards. Max size and page count configurable. | P0 |
| F-AI-2 | Generate a deck from a YouTube URL via transcript retrieval. Fail gracefully when no transcript exists. | P1 |
| F-AI-3 | Thoroughness setting (Concise / Standard / Thorough) controlling cards-per-page density. | P0 |
| F-AI-4 | Generation produces a *draft* deck. User reviews, edits, deletes, approves per card or in bulk before cards enter scheduling. | P0 |
| F-AI-5 | Each AI card stores a source reference (file, page or timestamp) and links back to it. | P0 |
| F-AI-6 | AI summary of a deck's source material; unlock threshold (default ≥20 cards, configurable). | P1 |
| F-AI-7 | Mock exam: AI-composed question set from the deck, optional timer, graded result with per-topic breakdown. | P1 |
| F-AI-8 | Chat with a deck's source documents, answers cite file and page. | P1 |
| F-AI-9 | Text-to-speech "podcast" of deck summary. | P2 |
| F-AI-10 | Generation runs as an async job with progress events (SSE) and is retryable and cancellable. | P0 |
| F-AI-11 | Per-user monthly AI page quota and per-instance spend cap. | P0 |
| F-AI-12 | Provider adapter interface; implementations: Anthropic (default), OpenAI-compatible endpoints (covers LM Studio, Ollama, vLLM). | P0 |

### 4.3 Cards

| ID | Requirement | Pri |
|---|---|---|
| F-CARD-1 | Basic (Question/Answer) with Markdown and KaTeX math. | P0 |
| F-CARD-2 | Cloze deletion with multiple numbered deletions per note. | P0 |
| F-CARD-3 | Image Occlusion: draw rectangles over an image; each mask becomes a card; edit masks later. | P1 |
| F-CARD-4 | Custom note types: user-defined fields and card templates (Anki-compatible subset). | P1 |
| F-CARD-5 | Images and audio attachments on cards. | P0 |
| F-CARD-6 | Tags, notes, suspend/bury, flag. | P1 |

### 4.4 Import and export

| ID | Requirement | Pri |
|---|---|---|
| F-IMP-1 | Import `.apkg` (Anki 2.1 and the newer collection format): notes, note types, cards, media, tags, decks. | P0 |
| F-IMP-2 | Preserve review history and scheduling state when present, mapped to our scheduler. | P1 |
| F-IMP-3 | Import selection by tag or sub-deck; dry-run report listing unsupported constructs. | P1 |
| F-IMP-4 | Export deck as `.apkg` and as CSV/JSON. | P1 |
| F-IMP-5 | Full account data export (GDPR-style) and account deletion. | P0 |

### 4.5 Scheduling and learning plan

| ID | Requirement | Pri |
|---|---|---|
| F-SRS-1 | Adaptive scheduler based on **FSRS** (open, published, per-user parameter optimization). Interface-isolated so the algorithm is swappable. | P0 |
| F-SRS-2 | Four-grade rating (Again/Hard/Good/Easy); maps from AI-evaluated or self-evaluated results. | P0 |
| F-SRS-3 | Per-user parameter training from review logs once enough history exists. | P2 |
| F-PLAN-1 | A *goal* has deck(s), exam date, desired grade (slider). | P0 |
| F-PLAN-2 | Planner computes the daily new + review load so that predicted retention on exam day meets the target. | P0 |
| F-PLAN-3 | Plan recomputes after missed days and after card additions; user sees what changed. | P1 |
| F-PLAN-4 | Exam-day readiness forecast (expected proficiency) shown on the goal and deck. | P1 |
| F-PLAN-5 | Decks without a goal use steady-state scheduling with a target retention (default 90%). | P0 |

### 4.6 Study experience

| ID | Requirement | Pri |
|---|---|---|
| F-STUDY-1 | **Self-evaluation** mode: reveal answer, rate. | P0 |
| F-STUDY-2 | **AI-evaluation** mode: type an answer; the model grades it against the reference and explains the gap. | P1 |
| F-STUDY-3 | Keyboard-first controls (space/1-4), undo last answer, edit card in session. | P0 |
| F-STUDY-4 | Voice mode: TTS reads the question, STT captures the answer, AI grades. | P2 |
| F-STUDY-5 | Offline review in the PWA with queued sync and conflict-safe merge of review logs. | P1 |

### 4.7 Decks, organization and sharing

| ID | Requirement | Pri |
|---|---|---|
| F-DECK-1 | Nested decks, rename, move, delete with confirm. | P0 |
| F-DECK-2 | Card browser: filter by state (new/learning/review/suspended), tag, proficiency; sort; search. | P0 |
| F-DECK-3 | Bulk actions: move, delete, tag, suspend. | P0 |
| F-SHARE-1 | Share a deck by unguessable link (read-only preview). | P0 |
| F-SHARE-2 | Owner can revoke the link at any time. | P0 |
| F-SHARE-3 | Per-link "allow cloning" toggle. Clones copy content, never review history. Recipients need an account (invite-only still applies). | P0 |

### 4.8 Progress and statistics

| ID | Requirement | Pri |
|---|---|---|
| F-PROG-1 | Daily goal (cards/day), streak, completion celebration. | P1 |
| F-PROG-2 | Deck proficiency percentage with progress ring. | P0 |
| F-PROG-3 | Review heatmap, retention curve, forecast of upcoming reviews. | P1 |
| F-PROG-4 | Deck stats: total, new, learning, due, mature, suspended. | P0 |

### 4.9 Admin console

| ID | Requirement | Pri |
|---|---|---|
| F-ADMIN-1 | Invite management (create, resend, revoke, list status). | P0 |
| F-ADMIN-2 | User management (suspend, role, delete, impersonation **disabled**). | P0 |
| F-ADMIN-3 | Quotas: AI pages/month per user, global default, instance spend cap. | P0 |
| F-ADMIN-4 | Usage and cost dashboard by user and provider. | P1 |
| F-ADMIN-5 | Immutable audit log of admin and security events. | P0 |

### 4.10 Onboarding and legal

| ID | Requirement | Pri |
|---|---|---|
| F-ONB-1 | Guided first run: accept invite → upload or import → verify cards → set exam date and grade → first session. Skippable. | P1 |
| F-LEGAL-1 | Privacy policy, terms and imprint pages (templates; the operator fills in). | P1 |
| F-LEGAL-2 | AI data-use disclosure: user content is never used for model training by this software; provider terms shown. | P0 |

## 5. Non-functional requirements

| ID | Requirement |
|---|---|
| NF-SEC-1 | OWASP ASVS L2 as the target. Threat model maintained in `docs/security/`. |
| NF-SEC-2 | Uploads: MIME sniffing, size/page limits, antivirus hook, sandboxed parsing in the worker, no execution of embedded content. `.apkg` parsed defensively (zip-bomb, path traversal, SQLite hardening). |
| NF-SEC-3 | Secrets only via environment or secret manager; no secrets in the repo; gitleaks in CI. |
| NF-SEC-4 | Strict CSP, HSTS, SameSite cookies, CSRF protection, input validation at the boundary, parameterized SQL. |
| NF-SEC-5 | Per-tenant authorization on every object access (deck, card, file, share link); tested with negative cases. |
| NF-SEC-6 | Prompt-injection hygiene: uploaded content is data, model output is validated against a schema, no tool access in generation calls. |
| NF-PERF-1 | Study-session card fetch p95 < 150 ms; answer submission p95 < 200 ms server-side. |
| NF-PERF-2 | 20-page PDF → draft deck in < 60 s p50 (provider-dependent), with streaming progress. |
| NF-REL-1 | Review submissions idempotent (client-generated IDs); no lost reviews on retry or offline sync. |
| NF-REL-2 | Daily database backups with tested restore; documented RPO 24 h, RTO 4 h. |
| NF-OBS-1 | Structured logs, request IDs, OpenTelemetry traces and metrics, health and readiness endpoints. |
| NF-A11Y-1 | WCAG 2.2 AA; full keyboard operation; screen-reader labels; reduced-motion respected. |
| NF-I18N-1 | All strings externalized; ship the 10 reference languages (English at launch, machine-drafted others reviewed before release). |
| NF-UX-1 | Minimal, calm, Notion-like layout; dark/light theme; mobile-first responsive. |
| NF-PRIV-1 | Data minimization; user export and delete; no third-party analytics by default. |
| NF-OSS-1 | Reproducible dev environment (`docker compose up`), one-command test run, semantic versioning, changelog. |
| NF-ENG-1 | Engineering principles in `docs/ENGINEERING.md` (Reuse first, KISS, DRY, SOLID, DDD) are enforced: import-linter layer contracts, ruff, mypy `--strict`, PR checklist. |
| NF-ENG-2 | Test coverage is at least 97% (lines and branches); the build fails below it. |

## 6. Architecture

### 6.1 Principles

The binding rules are in [docs/ENGINEERING.md](ENGINEERING.md): **Reuse first, KISS/YAGNI, DRY, SOLID and DDD** (Clean Architecture), with minimal dependencies and forward-compatible interfaces. Domain and application code import no framework; providers (AI, storage, mail, scheduler, queue) sit behind ports. The layer rules are enforced in CI by import-linter, not by convention.

### 6.2 Stack

| Layer | Choice | Rationale |
|---|---|---|
| API | **Python 3.12, FastAPI, SQLAlchemy 2 (async), Alembic, Pydantic v2** | Matches the maintainer's established stack; strong typing; first-class async and SSE. |
| Database | **PostgreSQL 16** (+ `pgvector` for document chat retrieval), hosted on Supabase | One datastore for relational data, job state and embeddings; used as plain Postgres so any Postgres works for self-hosters. |
| Jobs | **`JobQueue` port** with adapters: `InProcessJobQueue` (Phase 0), `PgmqJobQueue` on Postgres (Phase 1+) | No Redis to run, pay for or secure; fits the minimal-dependency principle. See ADR 0004. |
| Object storage | `ObjectStore` port over an S3-style API (Supabase Storage in hosted phases, MinIO locally) | Portable via port; no vendor lock-in. Supabase's S3 compatibility is to be verified in M0. |
| Frontend | **React 19 + TypeScript + Vite**, TanStack Query/Router, Tailwind, Radix primitives | Installable PWA, offline via service worker and IndexedDB. |
| Scheduler | `fsrs` library behind a `Scheduler` port | Open algorithm, swappable. |
| AI | `LLMProvider` port: Anthropic (default), OpenAI-compatible | Local-model friendly (LM Studio, Ollama). |
| Auth | Server-side sessions in secure cookies; Argon2id; WebAuthn | Avoid JWT pitfalls for a first-party app. |
| Observability | OpenTelemetry, structured JSON logs | Vendor-neutral. |
| CI/CD | GitHub Actions: ruff, mypy --strict, pytest, eslint, tsc, vitest, Playwright, gitleaks, CodeQL, Trivy, Dependabot | |
| Packaging | Docker images, docker-compose for dev and self-host | |

### 6.3 Hosting (invite-only instance)

Hosting is phased (ADR 0004) because the project starts free and non-commercial:

- **Phase 0 (testers, about $0):** one Render free web service running the API, which also serves the built frontend, with jobs running in-process; Supabase Free for Postgres and storage. Free services sleep after idle time and have small memory, so uploads are capped and jobs are resumable.
- **Phase 1 (about 20–100 invited users, about $32/month plus AI):** Fly.io machines for API and worker, Supabase Pro for Postgres, storage and the pgmq queue.
- **Phase 2 (commercial):** the same stack on larger plans, with billing added.

Everything outside the domain code sits behind a port, so moving between phases is configuration plus an adapter swap. Fallback: a single VPS with Docker Compose and Caddy. Deploys run from GitHub Actions; container images are published to GHCR.

### 6.4 Module layout (monorepo)

```
apps/api/        FastAPI app: domain/, application/, adapters/, api/
apps/worker/     arq jobs: ingestion, generation, import, planning
apps/web/        React PWA
packages/        shared OpenAPI-generated TS client, design tokens
docs/            PRD, ADRs, threat model, runbooks, API reference
deploy/          Dockerfiles, compose, fly.toml, Caddyfile
```

Bounded contexts: **Identity** (users, invites, sessions), **Content** (files, extraction), **Cards** (notes, note types, cards, decks), **Scheduling** (FSRS state, review log, plans), **AI** (generation jobs, chat, evaluation), **Sharing**, **Admin**.

### 6.5 Core data model (abridged)

`users`, `invites`, `sessions`, `audit_events` · `decks(parent_id)` · `note_types`, `notes`, `cards` · `media` · `sources`, `source_chunks(embedding)` · `card_sources` · `review_logs` (append-only) · `card_schedule` (derived, rebuildable from logs) · `goals`, `plan_days` · `share_links(token_hash, allow_clone, revoked_at)` · `ai_jobs`, `ai_usage` · `quotas`.

Review logs are the source of truth; scheduling state is a projection. This makes algorithm swaps and offline merges safe.

### 6.6 Key flows

1. **Generate:** upload → virus/MIME checks → store → `ai_job` queued → worker extracts pages → chunk + generate cards via schema-constrained output → draft deck + source refs → SSE progress → user approves.
2. **Review:** fetch due queue → client answers (idempotent ID) → server appends log, updates FSRS state → returns next. Offline: queue locally, replay on reconnect.
3. **Invite:** admin creates token (hash stored) → emailed link → signup verifies token, consumes it atomically, creates user.

## 7. UX principles

Calm, minimal, typographic. One primary action per screen. Keyboard first. Generation never blocks: users can leave and return. Every AI output is labeled, reviewable and reversible. Empty states teach the next step.

Key screens: Invite/sign-up, onboarding, Home (today's plan), Deck list, Deck detail (browser, stats, goal), Study session, Card editor (incl. occlusion editor), Import wizard, Mock exam, Chat, Share dialog, Settings, Admin console.

## 8. Testing and quality

- Unit tests on domain logic (scheduler, planner, cloze parsing, invite rules); property tests for the planner.
- Integration tests with real Postgres via Testcontainers; contract tests for LLM adapters using recorded fixtures.
- Golden-file tests for `.apkg` import using sample decks.
- End-to-end Playwright suites for invite → upload → review → share.
- Authorization matrix tests for every resource type.
- **Coverage gate: 97% of lines and branches across the whole codebase**, enforced in CI for the API (pytest-cov) and the web app (Vitest). Coverage is a floor: failure paths, authorization cases, deterministic tests and adapter contract tests are also required. Mutation testing on scheduler, planner and invite rules (P2).
- Evals for AI quality: card faithfulness to source, duplicate rate, grading agreement against a labeled set. Tracked per release.

## 9. Milestones

| Phase | Scope | Exit criteria |
|---|---|---|
| **M0 Foundation** | Repo, CI, docs, ADRs, compose dev env, auth + invites + admin skeleton, Phase 0 deploy to Render free (ADR 0004) | Invite-only signup works end to end on the deployed instance; CI green |
| **M1 Core learning** | Decks, Basic/Cloze cards, editor, FSRS review, self-evaluation, stats | A user can create and review cards daily |
| **M2 AI ingestion** | PDF/image upload, async generation, review/approve, source refs, quotas | 20-page PDF becomes approved deck |
| **M3 Goals and plan** | Exam date/grade, planner, readiness forecast, daily goals | Plan adapts after missed days |
| **M4 Import and media** | `.apkg` import/export, image occlusion, custom note types | Real Anki decks import cleanly |
| **M5 Advanced AI** | YouTube, AI-evaluation, summaries, mock exams, document chat | Feature parity P0+P1 complete |
| **M6 Sharing, PWA, i18n** | Share links, offline, 10 languages, a11y audit | Beta to invited users |
| **M7 Hardening and launch** | Pen test checklist, backups drill, load test, docs | Production release v1.0 |
| **Later** | Voice mode, podcasts, FSRS training, native apps | |

## 10. Risks

| Risk | Mitigation |
|---|---|
| Trademark/trade-dress similarity to Retain | Original name, branding, copy and visuals; this PRD references the product for feature scope only. |
| YouTube transcript access is unreliable or restricted | Treat as best-effort P1; support manual transcript paste; isolate behind a port. |
| AI cost overrun | Quotas, spend cap, token budgeting, caching of extraction results. |
| Hallucinated or unfaithful cards | Mandatory review step, source references, schema validation, eval suite. |
| `.apkg` format variance and malicious files | Defensive parser, fixtures from real decks, sandboxed worker. |
| Scope size | Strict phase gates; P2 items cut first. |
| Copyrighted uploads on a shared instance | Private by default, no cross-user content search, terms of use, takedown process. |

## 11. Open questions

1. Final repository name (requested: `falsh-cards`; likely meant `flash-cards`).
2. Custom domain for the hosted instance.
3. Email provider for invites (Resend, Postmark or SMTP).
4. Default AI provider budget per user.
5. Whether to ship non-English UI strings at launch or after native-speaker review.

## 12. Engineering best practices adopted

Intended for storage in Hindsight once connected. Full rules and how each is checked: [docs/ENGINEERING.md](ENGINEERING.md).

- **Reuse first:** search our own code, then the standard library, then a maintained open-source library, and only then write new code. Every new dependency is justified in the PR.
- **KISS and YAGNI:** the simplest design that meets today's requirement; no speculative abstraction. Add an abstraction at a real boundary or when a second implementation exists.
- **DRY:** one source of truth per business rule; merge duplicates only when they change for the same reason.
- **SOLID:** single-responsibility modules; extend through adapters and strategies; adapters honour their port's contract (contract tests); small role-specific ports; depend on abstractions and wire at the composition root.
- **DDD:** ubiquitous language from this PRD; bounded contexts (Identity, Content, Cards, Scheduling, AI, Sharing, Admin); rules inside entities and value objects; framework-free domain; repositories as domain-owned ports.
- **97% coverage floor** on lines and branches, with failure-path, authorization and contract tests on top.
- Architecture Decision Records for every non-trivial choice (`docs/adr/`).
- Trunk-based development, Conventional Commits, protected `main`, required review and checks.
- Review logs as append-only source of truth; derive scheduling state.
- Ports and adapters around every external dependency (LLM, storage, mail, scheduler).
- Idempotency keys on all mutating client-originated events.
- Treat all uploaded and model-generated content as untrusted input.
- Schema-constrained LLM outputs with validation and bounded retries.
- Secrets scanning, dependency updates and SAST on every PR.
- Migrations are forward-only and tested against a production-sized snapshot.
- Feature flags for unfinished vertical slices.
