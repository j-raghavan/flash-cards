# Flash-Cards

Open-source, AI-assisted spaced-repetition study platform. Upload lecture PDFs, photos of notes or a YouTube link, review the generated cards, set an exam date and target grade, and follow a daily plan.

> **Status:** pre-alpha, under active development. See [docs/PRD.md](docs/PRD.md) for the product requirements.

## Features

- AI card generation from PDF, PNG and JPEG files or a lecture transcript, with every card reviewed before it is scheduled and a source reference on each one
- Basic and Cloze cards with image and audio attachments
- FSRS-based adaptive scheduling, with deck stats and proficiency
- Exam goals with a daily study plan and a readiness forecast
- Self-evaluated and AI-evaluated study, deck summaries, timed mock exams with a per-topic breakdown, and chat with your source documents that cites the page
- Anki `.apkg` import, full account export and account deletion
- Invite-only access
- Provider-agnostic AI: Anthropic by default, or any OpenAI-compatible endpoint, including a local model

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
