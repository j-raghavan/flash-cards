# Contributing

Thanks for helping. Please read [docs/PRD.md](docs/PRD.md) first.

## Workflow

1. Open an issue before large changes.
2. Branch from `main`; keep pull requests small and focused.
3. Use [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, `chore:`).
4. Record non-trivial design choices as an ADR in `docs/adr/`.
5. All checks must pass: lint, type-check, tests, secret scan.

## Licence and CLA

The project is AGPL-3.0-only. Before your first pull request is merged, you must agree to the [CLA](CLA.md) by adding the line given there to the PR description. New source files start with `SPDX-License-Identifier: AGPL-3.0-only`.

## Engineering rules

Read [docs/ENGINEERING.md](docs/ENGINEERING.md). In short:

- **Reuse first:** existing code, then the standard library, then a maintained library, then new code.
- **KISS and YAGNI:** the simplest design that works; no speculative abstractions.
- **DRY, SOLID and DDD:** one source of truth per rule; extend through ports and adapters; business rules live in the domain, which imports no framework.
- Wrap every external dependency (LLM, storage, mail, scheduler, queue) behind a port.
- Justify each new dependency in the PR.
- Treat uploaded files and model output as untrusted input.
- **Coverage must stay at or above 97%** (lines and branches), including failure and authorization paths.
- CI runs ruff, mypy `--strict`, import-linter and pytest; all must pass.

## Security

Report vulnerabilities privately; see [SECURITY.md](SECURITY.md).
