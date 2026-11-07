# Engineering Principles

These rules apply to every change. Each one says how it is checked, because a principle nobody checks is a wish.

## 1. Reuse first

Before writing code, look for something that already does the job, in this order:

1. The project's own code. Search `domain/`, `application/` and shared packages before adding anything.
2. The standard library.
3. A well-maintained, widely used open-source library with a compatible licence.
4. Only then, write it ourselves.

Rules:
- A new dependency needs a one-paragraph justification in the PR: what it replaces, its licence (must be compatible with AGPL-3.0), its maintenance status and its size. Fewer dependencies is the default.
- Do not re-implement well-solved problems: password hashing (Argon2 library), spaced repetition (FSRS library), Anki parsing, auth primitives, markdown/KaTeX rendering.
- Do not wrap a library "just in case". Wrap it only behind a port when we may swap it (LLM, storage, mail, scheduler, queue).

Checked by: PR template checklist, review, Dependabot and licence scanning in CI.

## 2. KISS and YAGNI

- Choose the simplest design that meets today's requirement and keeps tomorrow's change cheap. No speculative features, flags, or abstraction layers.
- An abstraction is added when there are two real implementations or a clear boundary (an external system), not before.
- Prefer plain functions and small classes over frameworks and metaprogramming.
- Functions do one thing, stay short, and avoid deep nesting.

Checked by: review, ruff complexity and simplification rules (`SIM`), and the reviewer asking "what is the simpler version?".

## 3. DRY, without forced reuse

- One source of truth for each rule or piece of knowledge (for example, invite expiry lives in `domain/invites.py` only).
- Duplicated code is removed once the third copy appears, or earlier if it duplicates a business rule. Do not merge code that only looks alike but changes for different reasons.

## 4. SOLID

| Principle | What it means here |
|---|---|
| **S**ingle responsibility | One reason to change per module/class. Entities hold rules; services orchestrate; adapters talk to the outside world. |
| **O**pen/closed | Add behaviour by adding an adapter or strategy (a new `JobQueue`, `LLMProvider`, `Scheduler`), not by editing working code with new `if` branches. |
| **L**iskov substitution | Every adapter honours its port's contract. Contract tests run the same suite against every implementation. |
| **I**nterface segregation | Small, role-specific ports (`InviteRepository`, `Clock`), not one large repository. |
| **D**ependency inversion | Domain and application code depend on ports; adapters depend on the domain. Wiring happens once, at the composition root. |

Checked by: import-linter layer contracts (below), contract tests for adapters, review.

## 5. Domain-driven design

- **Ubiquitous language:** code uses the terms in the PRD (deck, note, card, goal, plan, invite, review). Names match the product.
- **Bounded contexts:** Identity, Content, Cards, Scheduling, AI, Sharing, Admin. A context talks to another only through its application interface, never its tables.
- **Entities and value objects:** rules live in the domain objects (for example `Invite.check_redeemable`), not in handlers. Value objects are immutable.
- **Domain purity:** the domain layer and the application layer import no web, ORM or settings framework.
- **Repositories and ports** are interfaces owned by the domain; SQL lives only in adapters.
- **Atomic invariants** (single-use invites, review idempotency) are enforced by the storage layer, not by hope.

Checked by: `import-linter` contracts in `apps/api/pyproject.toml`:
- layers: `api -> adapters -> application -> domain`
- the domain and application layers may not import `fastapi`, `starlette`, `uvicorn`, `pydantic`, `pydantic_settings`, `sqlalchemy`, or the outer layers.

A pull request that breaks a contract fails CI.

## 6. Testing and the 97% coverage gate

- **Coverage must be at least 97%, measured on lines and branches, for the whole package.** `pytest` fails below it (`fail_under = 97` in `apps/api/pyproject.toml`). The same threshold applies to the web app (Vitest) once it exists.
- Coverage is a floor, not the goal. It does not prove the tests are good, so we also require:
  - tests for failure paths and authorization (who must be refused), not just happy paths;
  - deterministic tests: injected clock, no sleeping, no network;
  - contract tests that run the same suite on every adapter of a port;
  - mutation testing on the scheduler, planner and invite rules (post-launch target) to confirm tests catch changes.
- Exclusions are limited to code that cannot run: `if TYPE_CHECKING:` blocks and `...` stubs in Protocols. Anything else needs a justification in the PR.
- A change that lowers coverage must add tests in the same PR.

## 7. Other standing rules

- Treat uploaded files and model output as untrusted input; validate at the boundary.
- Schema-constrained LLM output with validation and bounded retries.
- Idempotency keys on client-originated mutations.
- Secrets only in the environment; secret scanning on every PR.
- Forward-only, tested database migrations.
- Record non-trivial decisions as ADRs.

## Definition of done

A change is done when: tests pass at ≥97% coverage; ruff, mypy `--strict` and import-linter pass; new dependencies are justified; behaviour that changes a requirement is reflected in the PRD or an ADR; and the PR checklist is complete.
