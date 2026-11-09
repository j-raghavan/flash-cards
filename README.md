# Flash-Cards

Open-source, AI-assisted spaced-repetition study platform. Upload lecture PDFs, photos of notes or a YouTube link, review the generated cards, set an exam date and target grade, and follow a daily plan.

> **Status:** pre-alpha. The backend API and a first web client are functional. See [docs/PRD.md](docs/PRD.md) for the full product requirements.

## Features

- AI card generation from PDF, PNG and JPEG files or a lecture transcript, with live progress, retry and cancel, every card reviewed before it is scheduled, and a source reference on each one
- Basic, Cloze, Image Occlusion and custom note types (user-defined fields and card templates), with image and audio attachments
- Card browser with search and filters by state, tag, flag and proficiency, plus bulk move, delete, tag, suspend, bury and flag
- FSRS-based adaptive scheduling, with deck statistics and proficiency
- Exam goals with a daily study plan and a readiness forecast
- Self-evaluated and AI-evaluated study, deck summaries, timed mock exams with a per-topic breakdown, and chat with source documents with page citations
- Anki `.apkg` import, full account export and account deletion
- Invite-only access with administrator-issued invites, email verification, password reset and sign-in lockout
- Administrator user management (suspend, role changes, deletion) with an append-only audit log
- Deck sharing by revocable link, with optional cloning
- Provider-agnostic AI: Anthropic by default, or any OpenAI-compatible endpoint, including locally hosted models
- Installable web client (PWA): invite sign-up, sign-in, email verification and password reset; nested decks with statistics and a proficiency ring; keyboard-first study sessions (Space to reveal, 1 to 4 to rate); a Basic and Cloze card editor with Markdown and KaTeX preview; the card browser with bulk actions; and AI generation with live progress and draft approval
- Offline review: the app shell and recently opened decks stay available offline, and answers given offline are kept on the device and synced when the connection returns

## Roadmap

- Web client screens for exam goals and study plans, mock exams, document chat, deck sharing, Anki import and the admin console
- Interface translations
- Anki `.apkg` export
- Automatic YouTube transcript retrieval

## Stack

FastAPI, SQLAlchemy 2, PostgreSQL (including the job queue), React, TypeScript, Vite. The hosted instance starts on free tiers (Render + Supabase) and moves to Fly.io + Supabase Pro as usage grows; self-hostable with Docker Compose.

## Web client

The client lives in `apps/web` (React, TypeScript, Vite). It calls the API under `/api` on its own origin unless `VITE_API_BASE_URL` points elsewhere; in development, Vite proxies `/api` to the API at `http://localhost:8000` (override with `FLASHCARDS_API_PROXY_TARGET`).

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
