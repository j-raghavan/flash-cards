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
- **Promoting a user to admin, or unsuspending an admin,** publishes every deck they own to all members at once, including decks made while they were a member. The admin console warns before either change (web milestone).
- **Admins see other admins' decks** as library decks too, read-only like any member.

### Progress is per (user, card)

- **Schedules are keyed by user and card.** `card_schedule` gains `user_id` and its primary key becomes `(user_id, card_id)`.
  - The migration fills `user_id` from each card's `owner_id`, so every existing schedule becomes its owner's. No row is dropped or rewritten otherwise.
  - The schedule port takes the user: `get(user_id, card_id)`, `get_many(user_id, card_ids)`, `list_for_user(user_id)`, `upsert(user_id, schedule)` and `delete(user_id, card_id)` (undo of a first answer). Two deletes span users: `delete_for_card(card_id)` (every user's rows, used when a card is deleted or re-derived) and `delete_for_user(user_id)` (account deletion).
- **Review, undo and the due queue** read and write the caller's schedule. Review logs, the last-answer row and idempotency already work per reviewer.
- **Undo needs the card to be readable still.** Undoing an answer on a library card whose admin is no longer active, or on a card its owner has since suspended, answers 404 and changes nothing.
- **Deck stats, proficiency and the due forecast** count the caller's schedules over the cards they can read.
- **Bury and flag belong to the card's owner.** They are still columns on `cards`, so they are the owner's personal markers:
  - for a reader who does not own the card, the due queue and deck stats ignore `buried_until`, so an admin burying a card does not hide it from members;
  - every card payload returned to a non-owner reports flag `0` and no bury date.
- **A suspended card is hidden from everyone but its owner.** The admin suspends a card when it should not be studied at all, so for a member it is not in the deck's cards, the due queue, deck stats or its note's cards, and opening, answering or undoing it answers 404. The owner keeps seeing it as suspended, as today.

### Access

- **One access policy decides who reads a deck.**
  - *Readable* means the caller owns the deck, or an active admin owns it.
  - *Writable* means the caller owns it.
  - Read paths use readable:
    - deck list and deck get
    - deck stats, the due queue and review
    - card and note get
    - note types and media, for any active admin's, not only those a library card references. Their ids are random UUIDs and media is raster images and audio only (no SVG or HTML), so reading them by id discloses no more than the library cards already do.
  - Every write path keeps its owner check unchanged.
  - A card its owner has suspended is not readable by anyone else, and undo needs the answered card to be readable still (see above).
- **The deck list** answers the caller's decks plus the library decks. Each deck says whether the caller owns it (`owned`).
  - A library deck's parent is another library deck of the same admin, so the tree stays whole.
  - A member's own deck can never be placed inside a library deck: moving into a deck still requires owning it.

### What stays per owner for now (later milestones)

These are enhancements. Library decks simply do not offer them until they land, so the core works without them.

1. **Per-member hide or subscribe.** Members cannot hide a library deck yet, and its new cards have no per-day limit, so a large library fills every member's queue at once. A per-member choice of which library decks to follow comes first.
2. **Per-member suspend, bury and flag on library cards.** Until then, a card the admin suspends is hidden from everyone else, the admin's bury and flag are ignored for members, and members cannot suspend, bury or flag library cards.
3. **Library cards in the card browser, goals and the goal planner.** Until then, these show only the member's own cards. The due forecast already counts library cards.
4. **AI features on library decks:** document chat, deck summary, mock exams and typed-answer grading. Until then, they are offered on the member's own decks only.
5. **Export of library decks.** Members can still clone a library deck through a share link if the admin creates one.

### Deletion and privacy

- **Deleting a library deck, note or card, re-deriving a note so a card disappears, or deleting the admin's account** erases every member's schedules and review logs on those cards. The alternative, keeping history for cards that no longer exist, would leave rows nobody can study or see and complicate the export. The cost is that one admin action can wipe members' progress, so when an admin deletes a deck, a note, cards in bulk or an admin's account, the web confirmation says that members who studied it lose their progress on it. It gives no count of those members: no route reports one yet. A later enhancement may keep member history.
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
   - library decks on the home page. A member whose library has decks is not sent to the guided setup automatically; it is still offered.
   - a read-only deck page for library decks (study and stats only; no edit controls). Browsing library cards stays an enhancement.
   - warnings on the admin console before promoting a user or unsuspending an admin, and before demoting or suspending one, and on deletion of library material
   - the study screen without the edit shortcut on library cards
   - the offline queue unchanged, since reviews keep the same endpoint
3. **Enhancements,** each its own change, in this order: per-member hide or subscribe; per-member suspend, bury and flag; the browser, goals and the goal planner over library cards; AI features on library decks.

## Consequences

- Admin corrections reach every member immediately. A changed card keeps every member's progress unless its card set changes (cloze deletions added or removed), as for the owner today.
- The schedule table grows with members times library cards. Its primary key serves every per-user read.
- On a household server, the admin's decks are no longer private to the admin. That is the point of the feature, and it is stated on the admin console and in the runbook.
- The migration is additive (a new column backfilled from existing data, then the key changes). It runs on upgrade like every other migration, after `upgrade.sh` has taken a backup.
- The migration's downgrade refuses to run while any schedule belongs to someone other than the card's owner, since the old key cannot hold those rows. Restoring the pre-upgrade backup is the way back.
- The migration and the SQL adapters are verified against PostgreSQL on every change, not only against the in-memory adapters, because the key change and the per-user queries only show their behaviour on the real database.
