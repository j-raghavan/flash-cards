# Flash-Cards

Open-source, AI-assisted spaced-repetition study platform. Upload lecture PDFs, photos of notes or a YouTube link, review the generated cards, set an exam date and target grade, and follow a daily plan.

> **Status:** pre-alpha. The product requirements are written; implementation is starting at milestone M0. See [docs/PRD.md](docs/PRD.md).

## Planned capabilities

- AI card generation from PDF, PNG, JPEG and YouTube, with mandatory review and a source reference on every card
- Basic, Cloze, Image Occlusion and custom note types
- Anki `.apkg` import and export
- FSRS-based adaptive scheduling, exam-date planner and readiness forecast
- Self-evaluated and AI-evaluated study modes, summaries, mock exams and document chat
- Deck sharing by revocable link, with optional cloning
- Invite-only access with an admin console, quotas and audit log
- Provider-agnostic AI: Anthropic by default, or any OpenAI-compatible endpoint including local models
- Installable PWA with offline review

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
