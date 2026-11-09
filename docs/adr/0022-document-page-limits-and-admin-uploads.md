# ADR 0022: Document page limits and admin uploads

- **Status:** Accepted
- **Date:** 2026-11-09
- **Amends:** ADR 0016 (quotas), ADR 0017 (`FLASHCARDS_MAX_UPLOAD_PAGES`), ADR 0004 (upload cap)

## Context

The per-document page cap (`FLASHCARDS_MAX_UPLOAD_PAGES`) defaulted to 20. An ordinary lecture PDF of 22 to 34 pages was refused with 413 "The document has too many pages.", which named neither the count nor the limit, and the upload screen did not say what the limit was. A monthly quota refusal (429) was just as unspecific.

Admins also need to generate from documents of any length, such as a whole course pack, without lifting the cap for everyone.

The cap had been the only thing keeping a generation job's memory small: the worker collected every extracted page, rasterized images included, into a list before generating. At 20 pages that was tolerable; at hundreds it is not.

## Decision

### Memory no longer depends on the page count

- The worker streams pages. It counts them first (to set the job's total and check the cap), then extracts one page, generates its drafts, commits, and only then extracts the next. Peak memory is about the stored document (at most 25 MB) and MuPDF's parsed structure for it, plus one rendered page (at most 16 million pixels, ADR 0017) and its model request, whatever the page count.
- A resumed job tells the extractor how many pages to skip. Skipped pages are not rendered, so a retry of a long job does not rasterize the pages it already generated.
- Document chat extracts text only: a page without text is left out rather than rasterized, since retrieval cannot use it. The existing bound of passages per source still applies.
- The extractor is closed as soon as a caller stops early (a cancelled job, a failed page, the passage bound), so the parsed document is released at once.

### The member cap is 300 pages

- `DEFAULT_MAX_UPLOAD_PAGES` is 300. With memory bounded as above, the cap now bounds a job's duration and cost (one model call per page), not its memory. 300 covers long lecture decks and short books; operators can lower or raise it with `FLASHCARDS_MAX_UPLOAD_PAGES`.
- The 25 MB upload size cap, MIME sniffing, parsing in a single worker thread and the raster pixel cap are unchanged and apply to everyone.

### Admins have no page cap and no page quota

- For an active admin there is no per-document page cap, at request time or in the worker.
- Document chat indexes a source inside the chat request, so it keeps a bound for admins too: `MAX_CHAT_INDEX_PAGES` (2,000, or the member cap if that is higher). A longer source is recorded as having no text for chat; card generation from it is unaffected.
- An admin's generation is not refused by the monthly page quota. Their pages are still reserved in the same counter (against a limit of one billion pages, chosen so the reservation's `used_pages + pages` stays within the INTEGER column), so the usage report shows their pages next to their calls and cost.
- The instance monthly spend cap still applies to admins. It is the operator's guard on money, and a long admin job is exactly the work it should be able to stop.
- The role is read on every request, as for every other admin check, so an admin who is demoted is capped from their next request on.
  - The worker reads the job owner's role when it runs the job. A job queued by an admin who is demoted before it runs is held to the member cap and, if it is over it, fails with "The document has more pages than your limit allows." Its reserved pages are not refunded.
  - Pages counted while the user was an admin stay on their counter for the month. A demoted admin who used more than the member allowance is refused until the month rolls over, unless an admin raises their limit.
  - Chat reads the caller's role per question; a source that was over the cap when first indexed stays recorded as having no text.

### Messages say the numbers

- 413: "This document has 412 pages; the limit is 300." A transcript over the cap (possible only with a cap below 20) says how many parts it has instead.
- 429 for quota: "This document has 34 pages, but you have only 6 pages of AI generation left this month.", or that no pages are left this month, with the document's page count when known. A user whose limit is 0 is told that AI generation is not included in their account.
- `GET /account/ai-quota` adds `max_document_pages` (null for an admin) and `quota_applies` (false for an admin). The upload screen states the limits from it ("PDFs up to 300 pages, 25 MB.", the pages left this month, or that an admin has no page limit) and shows the server's message when an upload is refused. The account page tells an admin they have no monthly limit and how many pages they used.

## Consequences

- A member with the free tier's 10 pages a month can still not generate from a 34-page PDF; they now see why and how many pages they have left. The free-tier allowance (PRD R22) is unchanged.
- Extraction errors that appear only on a later page now fail the job after the earlier pages were generated and charged. Those drafts are kept, and a retry resumes after them.
- A long admin job holds the single PyMuPDF slot only while one page is parsed, so other users' uploads interleave with it, but the job itself can run for a long time. Cancellation is checked between pages, so a cancel takes effect after the page being generated and the next page's extraction.
- The worker opens the document once more than before (to count its pages, then to extract them). Counting is cheap compared with rendering.
