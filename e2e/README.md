# End-to-end tests

Playwright tests that drive the real web build against the real API, covering the journeys in PRD section 8. Design and the test-only AI provider: [ADR 0021](../docs/adr/0021-end-to-end-tests-and-fake-ai-provider.md).

## What runs

`npx playwright test` starts two servers and stops them at the end:

| Server | Command | Address |
| --- | --- | --- |
| API | `python -m uvicorn flashcards.api.app:app`, in-memory mode | `http://127.0.0.1:18765` |
| Web | `npm run build && npm run preview` in `apps/web` | `http://127.0.0.1:14173` |

The preview server's `/api` proxy forwards to the API, so the browser sees one origin, as in production. The API runs with:

- `FLASHCARDS_ENVIRONMENT=development`: the mailer logs every email in full
- `FLASHCARDS_DEV_BOOTSTRAP_INVITE=true`: global setup signs up the first admin with it
- `FLASHCARDS_LLM_PROVIDER=fake`: card generation returns two fixed drafts per page, one second per page; refused at startup unless the environment is `development` or `test` and no database URL is set
- no `FLASHCARDS_DATABASE_URL`: every run starts empty

The API's log is written to `e2e/.run/api.log`. Tests read emails (password reset links) from it.

## Journeys

| Spec | Covers |
| --- | --- |
| `core-journey.spec.ts` | An admin invites a member; the member signs up in the browser, creates a deck and a card, uploads a five-page PDF, watches live progress (Server-Sent Events) to the end, approves the drafts, studies with the keyboard (Space, 1 to 4) and undoes an answer, then shares the deck; a second user previews and copies it |
| `password-reset.spec.ts` | Forgot password, the emailed link, the new password works, the old one and a reused link do not |
| `card-browser.spec.ts` | Tag filter in the card browser, then a bulk suspend of the matches |
| `offline-review.spec.ts` | Answer with the browser offline, reconnect, and the review is recorded exactly once |
| `account-export.spec.ts` | Download my data from the account page, and the file's contents |

Every test signs up its own users, so tests are independent and run in parallel.

## Running locally

Prerequisites: the API's virtual environment in `apps/api/.venv` (see the main README) and Node 22 or later.

```sh
cd apps/web && npm ci
cd ../../e2e && npm ci
npx playwright install chromium
npx playwright test
```

- `E2E_PYTHON=/path/to/python` picks the API's interpreter; by default `apps/api/.venv/bin/python`, else `python`.
- `E2E_BROWSERS=chromium,firefox,webkit` adds the other browsers (install them first with `npx playwright install firefox webkit`).
- `E2E_API_PORT` and `E2E_WEB_PORT` move the servers if the default ports are taken.
- `npx playwright show-report` opens the last HTML report; failed tests keep a trace.

## Writing tests

- Arrange data through the API (`support/api.ts`) and drive the browser only for the journey under test.
- Use web-first assertions (`expect(locator).toBeVisible()`, `expect.poll`) and `waitForResponse`; never fixed sleeps.
- After a client-side navigation, wait for something only the new page has before typing into a field both pages share.
- Service workers are blocked so that API responses are never served from a cache.

## CI

The `e2e` job in `.github/workflows/ci.yml` runs the suite headless in Chromium with one retry. The job summary lists flaky tests (passed only on retry). On failure it uploads the HTML report, traces and the API log as the `e2e-report` artifact.
