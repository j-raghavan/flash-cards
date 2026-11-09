# Flash-Cards

Open-source, AI-assisted spaced-repetition study platform. Upload lecture PDFs, photos of notes or a YouTube link, review the generated cards, set an exam date and target grade, and follow a daily plan.

> **Status:** pre-alpha. The backend API is functional; the web client is in development. See [docs/PRD.md](docs/PRD.md) for the full product requirements.

## Features

- AI card generation from PDF, PNG and JPEG files or a lecture transcript, with live progress, retry and cancel, every card reviewed before it is scheduled, and a source reference on each one
- Basic, Cloze, Image Occlusion and custom note types (user-defined fields and card templates), with image and audio attachments
- Card browser with search and filters by state, tag, flag and proficiency, plus bulk move, delete, tag, suspend, bury and flag
- FSRS-based adaptive scheduling, with deck statistics and proficiency
- Exam goals with a daily study plan and a readiness forecast
- Self-evaluated and AI-evaluated study, deck summaries, timed mock exams with a per-topic breakdown, and chat with source documents with page citations
- Anki `.apkg` import, full account export and account deletion
- Invite-only access by default, with emailed invites that can be resent; self-hosters can open public sign-up
- Email verification, password reset, sign-in lockout, and a device list with sign-out per device or everywhere else
- Administrator user management (suspend, role changes, deletion) with an append-only audit log
- Monthly AI page quotas per user, an instance-wide monthly spend cap, and a usage and cost report by user and provider
- AI data-use disclosure: user content is never used for model training, and the configured provider and its terms are shown
- Deck sharing by revocable link, with optional cloning
- Provider-agnostic AI: Anthropic by default, or any OpenAI-compatible endpoint, including locally hosted models

## Roadmap

- Web client: installable PWA with offline review
- Anki `.apkg` export
- Automatic YouTube transcript retrieval

## Stack

FastAPI, SQLAlchemy 2, PostgreSQL (including the job queue), React, TypeScript, Vite. The hosted instance starts on free tiers (Render + Supabase) and moves to Fly.io + Supabase Pro as usage grows; self-hostable with Docker Compose.

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
