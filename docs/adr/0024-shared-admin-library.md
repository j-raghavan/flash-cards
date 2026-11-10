# ADR 0024: Shared admin library with per-member progress

- **Status:** Accepted
- **Date:** 2026-11-09

## Context

On a small instance (a household, a class) the admin prepares the study material and everyone else studies it. Today every deck is private to its owner. Share links (ADR 0009) copy a snapshot, so later corrections by the admin never reach the copies, and each member has to clone each deck by hand.

The operator wants every member to see every deck the admin creates, live, and to study it with their own progress.

Review state is coupled to the card's owner today:

- `card_schedule` is keyed by `card_id` alone.
- Suspension, bury and flag are columns on `cards`.
- Every read path checks `card.owner_id == caller`.

Review logs, the last-answer row and the review idempotency key are already per user.

## Decision

### What the library is

- **Library decks.** Every deck owned by an *active admin* is a library deck. There is no opt-in per deck. An admin keeps private material in another (non-admin) account if they need any.
- **Visibility to members.**
  - Members see library decks next to their own.
  - Library decks are read-only to members. Members can study them, see their stats and read their cards.
  - Only the owning admin edits, deletes, moves, generates into, shares or exports them.
- **When an admin is demoted, suspended or deleted,** their decks stop being library decks at once. Members' progress rows on those cards stay, unused, until the cards are deleted.
- **Admins see other admins' decks** as library decks too, read-only like any member.

### Progress is per (user, card)

- **Schedules are keyed by user and card.** `card_schedule` gains `user_id` and its primary key becomes `(user_id, card_id)`.
  - The migration fills `user_id` from each card's `owner_id`, so every existing schedule becomes its owner's. No row is dropped or rewritten otherwise.
  - The schedule port takes the user: `get(user_id, card_id)`, `get_many(user_id, card_ids)`, `list_for_user(user_id)`, `upsert(user_id, schedule)` and `delete(user_id, card_id)` (undo of a first answer). Two deletes span users: `delete_for_card(card_id)` (every user's rows, used when a card is deleted or re-derived) and `delete_for_user(user_id)` (account deletion).
- **Review, undo and the due queue** read and write the caller's schedule. Review logs, the last-answer row and idempotency already work per reviewer.
- **Deck stats, proficiency and the due forecast** count the caller's schedules over the cards they can read.
- **Bury and flag belong to the card's owner.** They are still columns on `cards`, so they are the owner's personal markers:
  - for a reader who does not own the card, the due queue and deck stats ignore `buried_until`, so an admin burying a card does not hide it from members;
  - every card payload returned to a non-owner reports flag `0`.
- **Suspension applies to everyone.** A card the admin suspends is hidden from every member's queue and stats, since the admin suspends a card when it should not be studied at all.

### Access

- **One access policy decides who reads a deck.**
  - *Readable* means the caller owns the deck, or an active admin owns it.
  - *Writable* means the caller owns it.
  - Read paths use readable:
    - deck list and deck get
    - deck stats, the due queue and review
    - card and note get
    - note types and media needed to render a card
  - Every write path keeps its owner check unchanged.
- **The deck list** answers the caller's decks plus the library decks. Each deck says whether the caller owns it (`owned`).
  - A library deck's parent is another library deck of the same admin, so the tree stays whole.
  - A member's own deck can never be placed inside a library deck: moving into a deck still requires owning it.

### What stays per owner for now (later milestones)

These are enhancements. Library decks simply do not offer them until they land, so the core works without them.

1. **Per-member suspend, bury and flag on library cards.** Until then, a card the admin suspends is suspended for everyone, the admin's bury and flag are ignored for members, and members cannot suspend, bury or flag library cards.
2. **Library cards in the card browser, goals and plans.** Until then, these show only the member's own cards.
3. **AI features on library decks:** document chat, deck summary, mock exams and typed-answer grading. Until then, they are offered on the member's own decks only.
4. **Export of library decks.** Members can still clone a library deck through a share link if the admin creates one.

### Deletion and privacy

- **Deleting a card, or editing a note so a card disappears,** deletes every user's schedule and review logs for that card. This is already the case for logs.
- **Deleting a member's account** deletes their schedules and logs on library cards as well as on their own.
- **Account export** includes the member's schedules and logs on library cards, by card id. The cards themselves stay the admin's.
- **Admins see member progress only in aggregate.** No admin screen shows another user's schedule or reviews.

## Delivery

1. **Backend core:**
   - migration and per-user schedule port
   - read access policy
   - deck list with `owned`
   - review, undo, due queue and stats on library decks
   - rendering reads (note types, media)
   - account delete and export
2. **Web:**
   - library decks on the home page
   - a read-only deck page for library decks (study, stats, browse their cards; no edit controls)
   - the study screen without the edit shortcut on library cards
   - the offline queue unchanged, since reviews keep the same endpoint
3. **Enhancements,** each its own change: per-member suspend, bury and flag; browser, goals and forecast over library cards; AI features on library decks.

## Consequences

- Admin corrections reach every member immediately. A changed card keeps every member's progress unless its card set changes (cloze deletions added or removed), as for the owner today.
- The schedule table grows with members times library cards. Its primary key serves every per-user read.
- On a household server, the admin's decks are no longer private to the admin. That is the point of the feature, and it is stated on the admin console and in the runbook.
- The migration is additive (a new column backfilled from existing data, then the key changes). It runs on upgrade like every other migration, after `upgrade.sh` has taken a backup.
- The migration's downgrade refuses to run while any schedule belongs to someone other than the card's owner, since the old key cannot hold those rows. Restoring the pre-upgrade backup is the way back.
- The migration and the SQL adapters are verified against PostgreSQL on every change, not only against the in-memory adapters, because the key change and the per-user queries only show their behaviour on the real database.
