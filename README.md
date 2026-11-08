# Flash-Cards

Open-source, AI-assisted spaced-repetition study platform. Upload lecture PDFs, photos of notes or a YouTube link, review the generated cards, set an exam date and target grade, and follow a daily plan.

> **Status:** pre-alpha. I've built the backend API through milestone M5. There is no web app yet, so for now it's an API you drive directly. See [docs/PRD.md](docs/PRD.md) for the full plan.

## What the API does today

- Invite-only accounts with sessions; admins create and revoke invites
- Nested decks, Basic and Cloze cards, image and audio attachments
- FSRS-based scheduling with self-evaluated reviews, deck stats and proficiency
- AI card generation from PDF, PNG and JPEG, or a pasted transcript, as an async job with a draft deck to review and approve, a source page on every card, and a monthly page quota
- Exam goals with a daily study plan and a readiness forecast
- Anki `.apkg` import, full account export and account deletion
- AI grading of typed answers, deck summaries, timed mock exams with a per-topic breakdown, and chat with a deck's source documents that cites the page
- Anthropic by default, or any OpenAI-compatible endpoint, including a local model

## Still to come

- The web app: an installable PWA with offline review
- Deck sharing by revocable link, with optional cloning
- Email verification, password reset, an admin console for users and quotas, and an audit log
- Image Occlusion, custom note types, and `.apkg` export
- Fetching YouTube transcripts automatically (for now you paste the transcript)

## Stack

FastAPI, SQLAlchemy 2, PostgreSQL (including the job queue), React, TypeScript, Vite. The hosted instance starts on free tiers (Render + Supabase) and moves to Fly.io + Supabase Pro as usage grows; self-hostable with Docker Compose.

## Documentation

- [Product requirements](docs/PRD.md)
- [Engineering principles](docs/ENGINEERING.md) (Reuse first, KISS, DRY, SOLID, DDD, 97% coverage)
- [Architecture decisions](docs/adr/)
- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)

## Independence notice

This project is an independent implementation based on publicly available information about the category of AI flashcard apps. It is not affiliated with or endorsed by any commercial product. All code, names and assets are original.

## License

AGPL-3.0-only. See [LICENSE](LICENSE). Contributions require agreement to the [CLA](CLA.md). Rationale: [ADR 0003](docs/adr/0003-licence-agpl-with-cla.md).
