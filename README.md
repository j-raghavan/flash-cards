# Flash-Cards

Open-source, AI-assisted spaced-repetition study platform. Upload lecture PDFs, photos of notes or a YouTube link, review the generated cards, set an exam date and target grade, and follow a daily plan.

> **Status:** pre-alpha. The backend API and a first web client are functional. See [docs/PRD.md](docs/PRD.md) for the full product requirements.

## Features

- AI card generation from PDF, PNG and JPEG files or a lecture transcript, with live progress, retry and cancel, every card reviewed before it is scheduled, and a source reference on each one
- Basic, Cloze, Image Occlusion and custom note types (user-defined fields and card templates), with image and audio attachments
- Card browser with search and filters by state, tag, flag and proficiency, plus bulk move, delete, tag, suspend, bury and flag
- FSRS-based adaptive scheduling, with deck statistics and proficiency, a review heatmap, a retention curve and a forecast of upcoming reviews, per deck and overall
- A daily goal with a streak and a completion signal, counted in the user's own time zone
- Exam goals with a daily study plan and a readiness forecast; the plan adapts to missed days and new cards and shows what changed and why
- Self-evaluated and AI-evaluated study with undo of the last answer, deck summaries, timed mock exams with a per-topic breakdown, and chat with source documents with page citations
- Anki `.apkg` import with review history and scheduling state, selective import by deck or tag with a dry-run report, and deck export as `.apkg`, CSV or JSON
- Full account export and account deletion
- Invite-only access by default, with emailed invites that can be resent; self-hosters can open public sign-up
- Email verification, password reset, sign-in lockout, and a device list with sign-out per device or everywhere else
- Optional two-factor sign-in: an authenticator app (TOTP, secrets encrypted at rest), passkeys (WebAuthn) as a second factor, ten single-use recovery codes, and an audited administrator reset for a lost device
- Administrator user management (suspend, role changes, deletion) with an append-only audit log
- Monthly AI page quotas per user, an instance-wide monthly spend cap, and a usage and cost report by user and provider
- AI data-use disclosure: user content is never used for model training, and the configured provider and its terms are shown
- Deck sharing by revocable link, with optional cloning
- Provider-agnostic AI: Anthropic by default, or any OpenAI-compatible endpoint, including locally hosted models
- Installable web client (PWA): invite sign-up and, where the instance allows it, public sign-up; sign-in with an optional second step (authenticator code, passkey or recovery code), email verification and password reset; nested decks with statistics and a proficiency ring; keyboard-first study sessions for every note type, including Image Occlusion and custom templates (Space to reveal, 1 to 4 to rate, U to undo the last answer, E to edit the card), with an AI-evaluation mode where a typed answer is graded, the gap explained and a rating suggested for confirmation; timed mock exams with a calm timer and a per-topic breakdown; deck summaries and chat with a deck's source documents with file and page citations, all labelled as AI output; a card editor for every note type with Markdown and KaTeX preview, an Image Occlusion mask editor and a custom note type editor; the card browser with bulk actions; AI generation with live progress and draft approval; exam goals with the daily plan, readiness and what changed after a replan; a progress page with the daily goal, streak, review heatmap, recall curve and review forecast; deck share links and shared-deck previews; an Anki import wizard with a dry-run preview; an account page with signed-in devices and sign-out of other devices, two-step sign-in setup (authenticator app with an in-browser QR code, passkeys, downloadable recovery codes), the monthly AI page quota, the AI data-use disclosure (also shown before a first upload), data export and account deletion; and an admin console for users with per-user AI quotas and two-step sign-in reset, invites with resend, the instance AI quota and spend cap, a monthly usage and cost report by user and by provider and model, and the audit log
- Offline review: the app shell and recently opened decks stay available offline, and answers given offline are kept on the device and synced when the connection returns

## Roadmap

- Undo for answers given offline, before they sync
- Interface translations
- Automatic YouTube transcript retrieval

## Stack

FastAPI, SQLAlchemy 2, PostgreSQL (including the job queue), React, TypeScript, Vite. The hosted instance starts on free tiers (Render + Supabase) and moves to Fly.io + Supabase Pro as usage grows; self-hostable with Docker Compose.

## Web client

The client lives in `apps/web` (React, TypeScript, Vite). It calls the API only on its own origin, under `/api` unless `VITE_API_BASE_URL` names another path; in development, Vite proxies `/api` to the API at `http://localhost:8000` (override with `FLASHCARDS_API_PROXY_TARGET`). In production, serve the built app and the API from one origin, with the API under `/api`. `apps/web/Dockerfile` builds an image that does exactly that (Caddy, configured by `apps/web/Caddyfile`), and the Docker Compose stack runs it as the site's entry point; see [Self-hosting with Docker Compose](docs/runbooks/self-hosting-compose.md).

The build adds a strict Content-Security-Policy as a `<meta>` tag. The web image also sends it as a header, as any other reverse proxy in front of the app should, since browsers ignore `frame-ancestors` in a `<meta>` tag:

```
Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; worker-src 'self'; manifest-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'
```

`style-src` allows inline styles because KaTeX positions math with style attributes; card text itself is sanitized and may not carry styles. Scripts are never inline.

```sh
cd apps/web
npm ci
npm run dev        # http://localhost:5173
npm test           # Vitest, fails below 97% line and branch coverage
npm run build
```

## Documentation

- [Product requirements](docs/PRD.md)
- [Engineering principles](docs/ENGINEERING.md) (Reuse first, KISS, DRY, SOLID, DDD, 97% coverage)
- [Architecture decisions](docs/adr/)
- [Self-hosting with Docker Compose](docs/runbooks/self-hosting-compose.md)
- [Threat model](docs/security/threat-model.md)
- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)

## Independence notice

This project is an independent implementation based on publicly available information about the category of AI flashcard apps. It is not affiliated with or endorsed by any commercial product. All code, names and assets are original.

## License

AGPL-3.0-only. See [LICENSE](LICENSE). Contributions require agreement to the [CLA](CLA.md). Rationale: [ADR 0003](docs/adr/0003-licence-agpl-with-cla.md).
