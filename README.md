# Flash-Cards

Open-source, AI-assisted spaced-repetition study platform. Upload lecture PDFs, photos of notes or a YouTube link, review the generated cards, set an exam date and target grade, and follow a daily plan.

> **Status:** beta. Feature-complete and self-hostable with Docker Compose.

## Features

- **AI card generation** from PDFs, images, pasted transcripts or YouTube links, with every card reviewed before it is scheduled and a source reference on each one.
- **Study** with FSRS scheduling, keyboard shortcuts, self-graded or AI-graded answers, timed mock exams, deck summaries and chat with source documents.
- **Note types:** Basic, Cloze, Image Occlusion and custom templates, with Markdown, math (KaTeX), images and audio.
- **Exam planning:** a daily plan and readiness forecast for an exam date and target grade, a daily goal and streak, and progress charts.
- **Decks:** nested decks, a read-only card reader, a card browser with bulk actions for admins, and Anki `.apkg` import and export (plus CSV and JSON).
- **Shared library:** an admin's decks are available to every member, read-only, with each member's progress kept separately.
- **Accounts:** invite-only by default, two-factor sign-in (authenticator app or passkey), per-device sign-out, full data export and account deletion.
- **Administration:** user management, an audit log, monthly AI quotas and an instance-wide spend cap with a usage report.
- **Appearance:** light, dark or follow the device, and four accent colours, saved per account.
- **Works offline:** installable as an app; recently opened decks can be studied offline and answers sync when the connection returns.
- **Any AI provider:** Anthropic by default, or any OpenAI-compatible endpoint, including locally hosted models. User content is never used for training.

## Roadmap

- A hosted instance
- Undo for answers given offline, before they sync
- Interface translations

## Stack

FastAPI, SQLAlchemy 2 and PostgreSQL (also the job queue), with a React, TypeScript and Vite web app. Self-hosting runs on Docker Compose; see [Self-hosting with Docker Compose](docs/runbooks/self-hosting-compose.md).

## Documentation

- [Product requirements](docs/PRD.md)
- [Engineering principles](docs/ENGINEERING.md)
- [Architecture decisions](docs/adr/)
- [Self-hosting with Docker Compose](docs/runbooks/self-hosting-compose.md) (also covers local development)
- [Threat model](docs/security/threat-model.md)
- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)

## Independence notice

This project is an independent implementation based on publicly available information about the category of AI flashcard apps. It is not affiliated with or endorsed by any commercial product. All code, names and assets are original.

## License

AGPL-3.0-only. See [LICENSE](LICENSE). Contributions require agreement to the [CLA](CLA.md). Rationale: [ADR 0003](docs/adr/0003-licence-agpl-with-cla.md).
