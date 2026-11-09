# ADR 0020: Guided first run, operator legal pages and YouTube transcript retrieval

- **Status:** Accepted
- **Date:** 2026-11-08

## Context

Three PRD items remained open:

- **F-AI-2** asks for a deck generated from a YouTube link. ADR 0007 put retrieval behind a `TranscriptProvider` port with a declining default (`NullTranscriptProvider`), so every link fell back to the manual paste. A real fetcher was left as "one more adapter".
- **F-LEGAL-1** asks for a privacy policy, terms and an imprint. The software cannot know who runs an instance or under which law, so the operator has to write them.
- **F-ONB-1** asks for a guided first run: upload or import, verify the cards, set an exam date and grade, then a first session, skippable throughout. PRD section 7 adds that empty states teach the next step.

## Decision

### YouTube transcripts: `youtube-transcript-api` behind a guarded transport

`adapters/youtube_transcripts.YouTubeTranscriptProvider` implements the port with [youtube-transcript-api](https://github.com/jdepoix/youtube-transcript-api).

**Why this library.** It is the maintained, widely used Python client for YouTube's caption tracks: regular releases that follow YouTube's changes (1.2.x at the time of writing), typed (`py.typed`), no API key, and its XML parsing goes through `defusedxml`. The alternatives are a YouTube Data API key (captions download needs OAuth as the video's owner, so it does not work for other people's videos) or scraping the pages ourselves, which is the same work the library already maintains.

**Licences.** youtube-transcript-api is MIT; its dependencies are `requests` (Apache-2.0) and `defusedxml` (PSF-2.0). All three are permissive and compatible with distribution under AGPL-3.0-only (Apache-2.0 is compatible with GPLv3 and so with AGPLv3). No copyleft or field-of-use terms are added. `types-requests` (Apache-2.0) is a development dependency only.

**Off by default.** `FLASHCARDS_YOUTUBE_TRANSCRIPTS_ENABLED=false`. Turned on, the server contacts youtube.com for every link a user submits, which an operator should choose knowingly (YouTube's terms, the server's IP reputation, and YouTube blocking many cloud IP ranges). Off, nothing changes from ADR 0007: the route answers 422 and points at the paste.

**Settings.**

| Setting | Default | Meaning |
|---|---|---|
| `FLASHCARDS_YOUTUBE_TRANSCRIPTS_ENABLED` | `false` | Use the fetcher instead of the declining stub. |
| `FLASHCARDS_YOUTUBE_TRANSCRIPT_LANGUAGES` | `en` | Comma-separated language tags, most preferred first. Validated at startup (at most 10, each like `en`, `de` or `pt-BR`). |
| `FLASHCARDS_YOUTUBE_TRANSCRIPT_TIMEOUT_SECONDS` | `10` | Connect and read timeout per request (above 0, at most 60). A whole fetch is cut off after three times this. |

**Language choice.** The library's own order: a manually written track in the first preferred language that has one, then a generated track. When no preferred language exists, the first available track (manual tracks first) is used rather than failing, since cards in the lecture's own language are more useful than none.

**No SSRF, by construction.** The user never supplies a URL that is fetched:

1. The domain parses the link (`parse_youtube_video_id`) and keeps only an 11-character id, matched with `fullmatch` so a trailing newline (`%0A`) cannot ride along. The adapter validates the id again before it builds any URL.
2. Every request the library makes goes through `GuardedYouTubeAdapter`, a `requests` transport adapter mounted for both `https://` and `http://`. It refuses anything but `https` to `www.youtube.com`, `youtube.com` or `m.youtube.com` on the default port without credentials. Caption track URLs come from YouTube's answer, and redirects are followed by `requests` through the same adapter, so both are checked on every hop: a caption URL or a redirect pointing at `169.254.169.254` or an internal host is refused before any connection. Redirects are capped at 2.
3. TLS is always verified, whatever the caller passes.

**Bounded in time and size.**

- Each request uses the configured connect and read timeout; the adapter ignores the caller's.
- A fetch may make at most 6 requests (watch page, a second one with the consent cookie, the player API, one caption track, room for a redirect) and must finish within three times the timeout. The deadline is checked before each request and while reading each body.
- Each body is streamed in 8 KB reads and capped at 5 MB, counted after decompression, so a compressed bomb is cut off too.
- The fetched transcript then passes the same domain checks as a paste or upload (ADR 0007): segment count, timestamps, and the 120,000-character cap. A transcript over the cap gets the existing "too long" 422.

**Checked and charged before the fetch.** `TranscriptImportService.from_youtube` checks the deck, the spend cap and that the user has at least one page of quota left, then charges one request against the daily AI request allowance (the counter synchronous AI features use, 200 a day by default). That bounds how often one user can make the server contact YouTube, and an over-quota user never causes a fetch. It then commits the unit of work, so the charge sticks even when the fetch fails and, in SQL mode, the request's database connection goes back to the pool during the network call.

**Off the event loop.** The library is synchronous, so the fetch runs in a worker thread (`anyio.to_thread.run_sync`) under its own `CapacityLimiter`, `TRANSCRIPT_FETCH` with 2 slots, next to the limiters of ADR 0017. Waiting for a slot is bounded: after 5 seconds without one the request gets the usual "no transcript, paste instead" answer instead of queueing. A single socket read can trickle past the adapter's deadline (the read timeout applies to each receive), so the request stops waiting at the deadline plus 2 seconds and leaves the thread behind (`abandon_on_cancel`). The slot belongs to the thread, not the request: the thread releases it when it ends, so abandoned fetches still count against the 2 slots and cannot pile up. If the request gives up before its thread starts, the request releases the slot and the thread, should it start later, does nothing.

**Known limitation.** If YouTube answers a server in some regions with a redirect to `consent.youtube.com`, the guard refuses it (the host is not on the list) and the user is offered the paste; the library expects the consent form inline on the watch page, which is what it handles. A fetched transcript over the 120,000-character cap gets the "too long" 422, and the web app does not move focus to the paste box for it, since pasting would not help.

**Failure.** Every library error (transcripts disabled, video unavailable, IP blocked, age restricted, malformed answer, a refused request, a timeout) becomes the domain's `TranscriptUnavailable`, logged server-side with the exception type only, and the user sees the existing 422 with the pointer to the paste. A transcript whose captions are all blank counts as none.

**Tests** run the real library offline: a subclass of the guard replaces only the final network send and answers from recorded fixtures (`tests/fixtures/youtube/`, trimmed to the fields the library reads). They cover the language choice, the host allowlist on caption URLs and redirects, the size cap, the request count, the deadline, the slot wait, and that the fetch runs in a worker thread.

### Legal pages: operator Markdown files with honest templates

- `FLASHCARDS_LEGAL_DIR` names a directory holding `privacy.md`, `terms.md` and `imprint.md`. They are read once at startup (`adapters/legal_pages.py`); editing a page takes a restart.
- A missing file falls back to a bundled template (`domain/legal.py`). Each template opens with a notice that the operator has not filled the page in, marks every operator-specific part with *Operator: ...*, and states only what the software itself does (one session cookie, no training on user content, export and deletion on the account page). The API returns `is_template: true` for it and the web app shows a banner above it. Without `FLASHCARDS_LEGAL_DIR` at all, startup logs a warning.
- A file that exists but cannot be served stops startup with the reason: over 200 KB, not UTF-8, empty, containing a NUL byte, or not a regular file; so does a configured directory that does not exist. A deploy mistake is caught at once instead of serving the template or an error to visitors.
- `GET /legal/{page}` is public. Only the three fixed names exist (anything else is 404), and the name never reaches a file path. Responses carry `Cache-Control: public, max-age=300`.
- The web app renders the pages at `/privacy`, `/terms` and `/imprint`, signed in or not, through the same `renderMarkdown` sanitizer as card text: DOMPurify with no scripts, forms, styles, ids or classes, images only from the app's own origin, and links opened with `noopener noreferrer`. Math rendering is switched off for these pages so prices such as "$5 or $10" stay as written. The operator is trusted to write the text, but not to inject script.
- Every screen has a footer with the three links, and both sign-up forms say that creating an account agrees to the terms and links the privacy policy.

### Guided first run: a short client-side flow

- A lazy-loaded `/welcome` route with five steps: start a deck (create one or pick an existing one), add material (generate from a file, write a card, or import from Anki), check the cards, set an exam date and grade (creates a goal for the chosen deck), and a first session. Each step can be skipped, and so can the whole flow.
- The flow links into the existing screens rather than copying them. Leaving for one of them moves the flow to the next step, and the home page shows "Continue setup" with the step number until the flow is finished or skipped.
- **When it appears.** A user who has no decks and has never started the flow on this device is taken to it once from the home page. A user with decks is never redirected; the account page has "Run the guided setup" to start over at any time. Finishing replaces the history entry, and opening `/welcome` after finishing (Back, an old link) says the setup is finished and offers to run it again rather than restarting it.
- **Where it is remembered.** The API has no per-user preference store, and adding one would need a migration, so the state (active or done, the step, the chosen deck) is kept in `localStorage` per user id. Every access is wrapped in `try`/`catch`; what is stored is validated and anything malformed counts as never started; a page-lifetime copy keeps the flow from reappearing during the same visit when storage is unavailable. Deleting the account clears it. A deck chosen earlier and deleted since is treated as no deck.
- **Empty states** now teach the next step: the empty deck list links to the guided setup, an empty deck names the ways to add cards, and the goals page explains what a goal does.
- The generate page also offers a YouTube link and a paste box, so F-AI-2 is reachable from the web app. When a link has no transcript, focus moves to the paste box. A paste over the 120,000-character limit is not cut short: the page says how long it is and disables sending.

## Consequences

- With the setting on, YouTube links produce decks without a paste when YouTube serves captions to the server's IP. It remains best-effort, as the PRD's risk table expects: YouTube changes and IP blocks surface as "paste instead", and keeping the library current matters more than for other dependencies.
- The fetch adds a network dependency to a request path, bounded by the slot wait, the per-request timeouts and the overall deadline, without holding a database connection. A burst beyond two concurrent fetches is turned away after 5 seconds rather than queued, and each attempt uses one of the user's daily AI requests.
- Operators must write their own legal pages; until they do, visitors see an unmistakable template rather than text that looks authoritative.
- Onboarding progress does not follow a user across devices. A user with no decks on a new device sees the flow again, which is harmless; moving the state server-side later needs only a preference endpoint and a different `state.ts`.
