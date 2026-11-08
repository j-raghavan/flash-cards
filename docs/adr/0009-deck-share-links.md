# ADR 0009: Deck share links

- **Status:** Accepted
- **Date:** 2026-11-08

## Context

F-SHARE-1 to F-SHARE-3 (all P0) ask for sharing a deck by an unguessable link with a
read-only preview, revoking the link at any time, and a per-link toggle that lets
recipients clone the deck. Clones copy content but never review history, and
recipients need an account because the instance is invite-only. The PRD data model
names `share_links(token_hash, allow_clone, revoked_at)`.

## Decision

- A share link belongs to one deck and its owner. Its token is 32 random bytes
  (URL-safe), returned once when the link is created and stored only as a SHA-256
  hash, the same scheme as invite tokens. A deck can have several links, so an owner
  who loses one creates another.
- The owner can list a deck's links, toggle `allow_clone`, and revoke a link. All of
  these are owner-scoped; another user's link answers 404 (NF-SEC-5). A revoked link
  cannot be changed (404).
- `GET /shared/{token}` returns a read-only preview: the deck tree in pre-order as
  names with their depth (up to 200 decks, with the total deck count) and a sample of
  up to 200 notes with each field trimmed to 500 characters, plus the total note
  count, computed without loading every note. It requires a signed-in account,
  which keeps an invite-only instance from serving content to anonymous visitors,
  and it never exposes the owner's identity.
- `POST /shared/{token}/clone` copies the deck subtree into the caller's library as
  a new top-level deck tree: decks keep their names and nesting, and each note is
  re-created, so its cards are derived again from its fields. Schedules, review
  logs, suspension state, AI jobs and sources are not copied. Sibling decks keep
  their order. A link with cloning disabled answers 403.
- A clone is bounded before anything is written: at most 500 decks, 20,000 notes and
  20,000 cards (cloze notes can yield many cards each), checked from counts before
  the notes are loaded, otherwise 413. Every note is validated first, so a clone is
  never left half-done. Each user may create up to 50,000 cards a day by cloning
  (429 after that), and clone requests share the bounded concurrency pool used by
  the synchronous AI routes, so a burst cannot take every database connection.
- Links of a suspended or deleted owner stop serving (404).
- A revoked or unknown token answers 404 for both preview and clone.
- Deleting a deck deletes its links (for the whole subtree), deleting an account
  deletes every link the account created, and the account export lists the links
  without their tokens.

## Consequences

- Sharing works without exposing any account details, and revoking takes effect
  immediately.
- The token travels in the URL path, so it can appear in access logs; it grants only
  read access and cloning, and the owner can revoke it at any time.
- A clone is a snapshot. Later edits to the original deck do not reach clones.
- The shared subtree is live: moving a deck under a shared deck shares it through the
  existing links, and moving it out stops sharing it. Showing the owner which decks a
  link currently covers is left to the client.
- Media attachments are owner-scoped, so if a note field ever references an
  attachment, a recipient cannot fetch it; copying referenced media is left for when
  notes reference media formally.
- Clones insert row by row inside one request; bulk inserts are a later optimization.
