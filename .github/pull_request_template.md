## What and why

<!-- One or two sentences. Link the issue or PRD requirement ID (for example F-AUTH-5). -->

## Checklist

- [ ] I have read the CLA in `CLA.md` and agree to its terms (first PR only)
- [ ] **Reuse first:** I looked for existing code, the standard library or a maintained library before writing new code
- [ ] **KISS/YAGNI:** this is the simplest design that meets the requirement; no speculative abstractions
- [ ] **DRY:** no duplicated business rule
- [ ] **SOLID:** new behaviour is added through a port/adapter or strategy; ports stay small
- [ ] **DDD:** rules live in the domain; domain and application layers import no framework; names match the PRD language
- [ ] Tests added, including failure and authorization paths; coverage stays at or above 97%
- [ ] `ruff`, `mypy --strict` and `lint-imports` pass
- [ ] Any new dependency is justified below (replaces what, licence, maintenance)
- [ ] PRD or ADR updated if a requirement or decision changed

## New dependencies (if any)

<!-- Name, licence, why it is needed, what it replaces. -->
