# ADR 0014: Anki round trip: review history, selective import, dry run and export

- **Status:** Accepted
- **Date:** 2026-11-08

## Context

The requirements:

- F-IMP-2 (P1): preserve review history and scheduling state from an `.apkg` when present, mapped to our scheduler.
- F-IMP-3 (P1): import a selection by tag or sub-deck, and a dry-run report that lists unsupported constructs.
- F-IMP-4 (P1): export a deck as `.apkg`, CSV and JSON.

The `.apkg` import (F-IMP-1) brings notes, tags and decks across, but every card arrives as new, Anki's nested decks (`Languages::Spanish`) become flat decks named with the full path, and there is no way to preview an import or take part of a package. Nothing can be exported except the full account export.

Anki schedules with SM-2 (or its own FSRS) and stores state in `cards` columns (`type`, `queue`, `due`, `ivl`, `factor`, `reps`, `lapses`) plus one `revlog` row per answer. We schedule with FSRS (ADR 0005, `CardSchedule`, `ReviewLog`), and review logs are our source of truth: the schedule is a projection of them. An `.apkg` is untrusted input (NF-SEC-2).

## Decision

### Reading packages

- The reader also reads each card's id, scheduling columns, original deck (`odid`) and flag, the collection's creation time (`col.crt`), each note type's template count, each deck's filtered flag, the `revlog` rows of cards in the package, and the number of media files.
- Columns are probed with `pragma_table_info`. A collection without the scheduling columns or without `revlog` still imports, with every card new.
- Anki 2.1.50 and later export `collection.anki21b` (zstd-compressed) next to a stub `collection.anki2` that holds only a "please update Anki" note. Such a package is refused (422) rather than importing the stub. The user can export again with "Support older Anki versions" ticked.
- New bounds, on top of the existing ones (archive entries, sizes and ratio, a 200 MB collection, 100,000 notes, 200,000 cards, a 40 MB upload):
  - At most 300,000 `revlog` rows. Past that, the whole history is dropped rather than failing the import: cards fall back to their SM-2 columns and the report says so.
  - `col`, `notes`, `cards` and `revlog` must be real tables. A view of the same name could hide an expensive query, so it is refused.
  - `trusted_schema` is off, and a SQLite progress handler interrupts the reads after a fixed budget of virtual-machine steps (one billion in total). A collection at the row limits uses a small fraction of it.
  - Malformed or deeply nested JSON in `col` is an invalid package, never a server error.
  - A cloze number above 1,000 (or too long to parse) makes the note invalid. Our ordinals are 32-bit integers.
  - A second note with the same guid is skipped, so two notes can never claim the same Anki cards.
  - Anki deck names nest at most 8 levels. Deeper levels stay in the name of the eighth deck, so a hostile `a::a::a::...` name cannot create a deck per level.

### Scheduling state (F-IMP-2)

The mapping is conservative: it never makes a card look better known than its history shows.

- **History is the authority.** Each `revlog` answer becomes a `Rating` and a `ReviewLog`, and the answers are replayed in time order through our `Scheduler`. The resulting schedule is exactly what our own reviews would have produced.
  - `ease` 1 to 4 maps to Again, Hard, Good, Easy, the way the FSRS optimizer reads it. Anki's old v1 scheduler offered three buttons while learning (3 meant Easy). That reads as Good here, which can only underestimate the card.
  - Rows of kind learn, review, relearn and filtered are answers. Manual changes (kind 4), reschedules (kind 5), `ease` 0, and times before 2006 or in the future are skipped and counted in the report.
  - A manual row (Forget, or an old-style set due date) is a reset. The answers before it are still imported as history but are not replayed, so a relearned card is scheduled from its latest learning only.
- **A card that is new in Anki** (`type` 0, including one reset to new) arrives without a schedule. Its history is still imported.
- **Without usable history**, only a graduated review card (`type` 2, positive `ivl`) gets a schedule from its SM-2 columns:
  - state review
  - stability = `ivl` in days, capped at 36,500. At our 90% retention an FSRS interval roughly equals stability.
  - difficulty = `5 + (2500 - factor) / 240`, clamped to 1 to 10. The default ease of 250% is difficulty 5; Anki's floor of 130% is 10.
  - due on day `due` counted from the collection's creation day (midnight UTC), but never later than one interval from now. A card borrowed by a filtered deck uses its home deck's due (`odue`), since its `due` there is only a queue position. If the creation day is missing or implausible, the card is due now.
  - last review = due minus the interval, never later than now and never before 2006.
  - Learning and relearning cards without history arrive as new.
- `queue` -1 suspends the card. The low three bits of `cards.flags` become its flag. Bury is transient in both apps and is not carried over.

### Mapping notes and cards

- Anki's nested deck names become nested decks: `Languages::Spanish::Verbs` creates `Languages` > `Spanish` > `Verbs`, each level cleaned (blank becomes `Imported`). Packages that used to create one deck per Anki deck id now create one deck per distinct path.
- A note lands in its first card's deck. A card that sits in a filtered deck goes back to its original deck.
- A note's Anki cards match our cards by ordinal: Basic `ord` 0 is our ordinal 0, and a cloze card's `ord` n is our deletion n + 1. Anki cards with no counterpart (a second template, an empty cloze deletion) are left out and reported, and so are their reviews.

### Selection and dry run (F-IMP-3)

- `POST /import/apkg` takes repeatable form fields `deck` and `tag` and a `dry_run` flag.
  - A note is selected when its deck is one of `deck` or a sub-deck of one (ignoring case), and it carries one of `tag` or a descendant tag. An empty list means no filter.
  - At most 50 decks and 50 tags, each 1 to 200 characters. Otherwise the request answers 422.
- The import is planned before anything is written: selection, mapping, our note validation and card matching. A dry run returns that plan and writes nothing (200). A real import executes the same plan (201).
- Reading the package, planning and replaying histories run in a worker thread (`asyncio.to_thread`), so one large import does not stall other requests. The FSRS adapter passes a fixed card id to py-fsrs, which otherwise sleeps 1 ms for every card it builds.
- The report adds `notes_not_selected`, `cards_scheduled`, `reviews_imported`, `dry_run` and `unsupported`. Each `unsupported` entry has a kind, a count and up to three short examples (note type or deck names from the caller's own package). The kinds:
  - `unknown_note_type`, `unmappable_note`, `invalid_note` and `duplicate_note`: skipped notes
  - `dropped_fields`: non-empty fields beyond the ones we map
  - `extra_cards`: Anki cards with no counterpart
  - `filtered_deck`: cards moved back to their original deck
  - `media`: media files, which the import does not bring across
  - `note_without_cards`
  - `skipped_reviews` and `history_truncated`

### Export (F-IMP-4)

- `GET /decks/{deck_id}/export?format=apkg|csv|json` (default `apkg`) downloads the deck with all its subdecks. It answers 404 for a deck that is missing or not the caller's, 413 above 200,000 cards, and 422 for an unknown format. Responses are attachments with a sanitised ASCII file name and `Cache-Control: no-store`.
- **Ownership:** the root's ownership is checked, the tree is built from the caller's decks only, and notes, cards and review logs are filtered by owner again, so an export never carries another user's data.
- **`.apkg`:** an Anki 2.1 legacy collection (`collection.anki2`, schema 11) with an empty media manifest, the format genanki writes and every Anki version imports.
  - Two bundled note types: `Basic (Flash-Cards)` (Front, Back) and `Cloze (Flash-Cards)` (Text, Back Extra).
  - Decks keep their nesting. A `::` inside one of our deck names becomes `:`.
  - The note guid is our note id, so importing the same export into Anki again updates notes rather than duplicating them.
  - Fields keep their markup. A `\x1f` (Anki's field separator) inside a field becomes a space. Basic fields other than front and back are left out, as in CSV.
  - Schedules are written as SM-2 columns:
    - a new card gets its queue position
    - a learning or relearning card gets its due time in epoch seconds, with one learning step left so Anki does not graduate it on its next Good
    - a review card gets a day number from the collection's creation day, chosen no later than any due date (and not before 2006) so day numbers are not negative
    - the interval is the gap between the last review and the due date
    - the ease is the inverse of the import mapping, so a round trip keeps difficulty
    - `reps` and `lapses` are derived from the history
  - Every review log becomes a `revlog` row. Ids are made unique by the millisecond. The row kind is derived from the answers: learning until the first Good or Easy, then review, with an Again on a review starting relearning until the next pass.
  - Exporting and re-importing replays the same history, so a card comes back with the same schedule.
- **CSV:** one row per note with `deck`, `note_type`, `front`, `back`, `text`, `tags`. A cell starting with `=`, `+`, `-`, `@`, tab or carriage return gets a leading `'`, so a spreadsheet does not run it as a formula (OWASP CSV injection guidance).
- **JSON:** `{"format": "flash-cards-deck", "version": 1, ...}` with every deck path and every note's fields, tags and cards, including each card's suspension, flag, schedule and review history. It is the lossless format.
- The writer is a port (`DeckExportWriter`) with one adapter for all three formats, so the application layer stays free of SQLite and zip code.

## Consequences

- Imported cards keep their place in the user's learning, and our statistics and forecasts include their history from the first day.
- Re-scheduling replays every answer through FSRS. Its cost grows with the history (bounded by the 300,000-row limit; about 0.7 seconds for 60,000 answers) and runs in a worker thread inside the import request.
- Writes are still one statement per row, inside the request's transaction, as in the original import. A package at the row limits means hundreds of thousands of statements on one connection. Batched inserts, or moving large imports onto the job queue, can replace that.
- The SM-2 fallback is an approximation. Anki's day boundary (4 a.m. local by default) is not applied, so a due date can be off by up to a day.
- Media files are still not imported or exported. Fields that reference them keep their markup, and the dry run reports how many files were left behind.
- Export loads the owner's review logs and filters them in the application, so both storage modes share one implementation. A `ReviewLogRepository` query by card ids can replace that if histories grow large.
- CSV is lossy by design (only the mapped fields, no schedule). Notes with extra fields keep them in JSON only.
- The exported `.apkg` has been checked by reading it back with our own reader and by opening it with SQLite. It follows the published schema 11 layout.
