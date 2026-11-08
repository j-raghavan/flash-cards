# ADR 0011: Card browser, tags, bury, flags and bulk actions

- **Status:** Accepted
- **Date:** 2026-11-08

## Context

The requirements:

- F-DECK-2 (P0): a card browser that filters by state (new, learning, review, suspended), tag and proficiency, and can sort and search.
- F-DECK-3 (P0): bulk move, delete, tag and suspend.
- F-CARD-6 (P1): tags, suspend and bury, and flags.

Anki users expect these to behave the way Anki does, and `.apkg` files carry tags. Until now, cards could only be suspended, notes could not be edited, and imported tags were dropped.

## Decision

### Tags

- Tags belong to notes, as in Anki, so every card of a note shares them.
- A tag is 1 to 64 characters with no whitespace. `::` separates levels, as in Anki (`biology::cells`).
- A note has at most 32 tags. Duplicates are dropped ignoring case, and tags are kept sorted.
- Filtering by a tag also matches its descendants: `biology` matches `biology::cells`.
- Imported tags are kept. Invalid ones are skipped rather than failing the import.

### Note editing

- `PATCH /notes/{note_id}` changes a note's fields, tags or both.
- Changing fields re-derives the note's cards:
  - Cards whose ordinal survives keep their schedule and history.
  - A cloze deletion that disappears takes its card, schedule and history with it.
  - A new cloze deletion adds a new card.
- The note type cannot change.

### Flags and bury

- A card has a flag from 0 (none) to 7, matching Anki's seven colours.
- Burying hides a card from the review queue until a given time. The client sends the start of the user's next local day, since the server does not know the user's time zone. Without a time, the card is buried for 24 hours. A bury lasts at most 7 days.
- Suspension and bury are independent. Unburying clears the bury early.

### Browser

- `GET /cards` searches the caller's cards. It can be scoped to a deck, with its subdecks by default, and filtered by:
  - state (new, learning, review, suspended, buried)
  - tag
  - flag
  - proficiency range
  - a text query, matched case-insensitively against the note's fields
- Each row carries what a browser table shows:
  - ids, deck and ordinal
  - note type and a short preview of the first field
  - tags, flag and state
  - due time and proficiency
- A card's state is suspended if suspended, otherwise buried if buried now, otherwise taken from its schedule (no schedule means new).
- A card's proficiency is its current probability of recall, as a percentage computed from FSRS stability and the time since the last review. A new card is 0. The deck-level proficiency in deck stats (the share of graduated cards) is unchanged.
- Sort keys: created (the default), due, proficiency or deck, ascending or descending, with the card id as the final tie-breaker so pages are stable.
- Paging uses `limit` (1 to 200) and `offset`. The response reports the total number of matches.

### Bulk actions

- `POST /cards/bulk` applies one action to up to 1,000 cards. The actions:
  - move to a deck
  - delete
  - add tags or remove tags
  - suspend or unsuspend
  - bury or unbury
  - set a flag
- Move, delete and tag act on the notes of the selected cards, so a note's cards always stay together in one deck. The rest act on the selected cards themselves.
- Every id is checked before anything changes. If any card is missing or belongs to someone else, the whole request answers 404 and nothing is applied. The response reports how many cards and notes were affected.

## Consequences

- The browser filters in the application, over the caller's cards loaded in three batched queries (cards, notes, schedules), so both storage modes share one implementation. That is fast enough for the clone and import limits (20,000 cards). Pushing the filters into SQL is a later optimization if collections grow beyond that.
- Moving a note moves all its cards. Anki allows the cards of one note to sit in different decks, and that is not supported.
- A bury time is absolute. A client that sends nothing gets 24 hours, which can end mid-morning instead of at the start of the user's day.
