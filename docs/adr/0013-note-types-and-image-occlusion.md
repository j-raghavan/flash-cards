# ADR 0013: Custom note types and Image Occlusion

- **Status:** Accepted
- **Date:** 2026-11-08

## Context

The requirements:

- F-CARD-4 (P1): custom note types with user-defined fields and card templates, an Anki-compatible subset.
- F-CARD-3 (P1): Image Occlusion. Rectangles are drawn over an image, each mask becomes a card, and masks can be edited later.

Until now a note was Basic (one card) or Cloze (one card per deletion), and a card was identified within its note by an ordinal. ADR 0011 made note edits re-derive cards: a card whose ordinal survives keeps its schedule and history. Many features read notes: sharing, `.apkg` import, AI drafts, AI evaluation, mock exams, summaries, the card browser and account export.

## Decision

### Note types

- `NoteType` gains two values: `custom` and `image_occlusion`.
- A custom note stores `note_type_id`, the id of its custom type. An image occlusion note stores `occlusion`: an image and its masks. Each is set for its own type only.
- The note type of a note still cannot change.

### Custom note types

- Custom note types are owner-scoped, with CRUD at `/note-types`. An owner has at most 100.
- A type has:
  - a name of 1 to 100 printable characters
  - 1 to 64 fields
  - 1 to 16 card templates
- Field names are 1 to 64 characters, unique ignoring case, without surrounding spaces, and without `{ } : # / ^ "`. `FrontSide` is reserved.
- A template has a name (1 to 64 characters, unique ignoring case), a front and a back, each at most 10,000 characters.
- The template language is a strict subset of Anki's:
  - `{{Field}}` substitutes a field's value.
  - `{{FrontSide}}`, on the back only, substitutes the rendered front.
  - Nothing else may appear in double braces: no conditionals, filters, `type:` or `cloze:`. A stray `{{` or `}}` is refused.
  - Every front uses at least one field, and every placeholder must name a field of the type.
- Each template has a stable ordinal, which is the ordinal of its cards:
  - New templates get ordinals above every current template, so a new template never takes over another template's cards. An ordinal freed by a template removed in an earlier edit may be given out again; its cards were purged with that template.
  - On edit, a template sent with its `ordinal` keeps it, and with it its cards.
- A custom note's fields are the type's fields, in the type's order. Unknown fields are refused, and missing ones are stored empty.
- A note gets a card for each template whose front uses at least one non-empty field, as Anki skips empty fronts. A note that would get no card is refused.

### Editing a type

- `PATCH /note-types/{id}` changes the name, fields or templates. Omitted parts stay.
- `renamed` maps old field names to new ones, and notes keep their values under the new name. It needs the new field list in the same request. A field cannot be renamed onto another field that keeps its values (two fields may swap names). A field left out of the new list is dropped from every note, and a new field starts empty.
- A name-only edit leaves notes and cards untouched.
- Templates that are not resent are checked again against the new fields, so renaming a field the templates use needs the updated templates in the same request.
- An edit of fields or templates re-derives the cards of every note of the type, as a note edit does (ADR 0011):
  - A card whose template survives keeps its schedule and history.
  - A removed template takes its cards, schedules and history with it.
  - A new template adds cards.
- Every note is checked before anything is written. If the edit would leave any note without a card, nothing changes (422).
- A type that notes still use cannot be deleted (409). In SQL, the `notes.note_type_id` foreign key is `ON DELETE RESTRICT` as a backstop.

### Image Occlusion

- An image occlusion note references one image the owner uploaded (`/media`). It must exist, belong to the caller and be an image, or the request answers 404 (not found or not the caller's) or 422 (not an image).
- It has 1 to 100 rectangular masks. A mask has:
  - an `id` from 1 to 999
  - `left`, `top`, `width` and `height`, normalized to the image size (0 to 1), finite, with a positive size, and lying within the image
- The mask id is stable and is the ordinal of the mask's card. Masks that share an id are one card, as Anki groups shapes. Editing masks follows the ordinal rule: a card whose mask id survives keeps its schedule, even if the mask moved.
- The image can be changed on edit; a new image is checked the same way.
- The only text fields are `header` and `back_extra`, both optional.
- The front hides every mask and marks the card's own masks; the back reveals the card's masks. This is Anki's default "hide all, guess one" mode. Clients may offer other modes, since they receive every mask.

### Rendering

- Clients render. The API returns what they need:
  - `GET /cards/{id}` returns the note's fields and type, plus the card's `template` for a custom card, or the `occlusion` (image id and every mask) for an image occlusion card. The card's own masks are those whose id equals its ordinal.
  - `GET /notes/{id}` returns `note_type_id` and `occlusion`, and `GET /note-types/{id}` returns the whole type.
- Templates and field values are untrusted HTML. Clients sanitize the rendered result before showing it, as they already must for imported Anki fields. The server never renders templates, so a template cannot inject into a server response.

### How other features treat the new types

| Feature | Custom notes | Image occlusion notes |
| --- | --- | --- |
| Share preview | Shown with their fields; the template is not part of the preview. | Shown with their text fields only. The image is never exposed through a share link. |
| Share clone | The note types the notes use are copied to the recipient, once per clone, and count toward the recipient's note type limit. | The images are copied to the recipient, once per image, and capped at 100 MiB per clone (413 beyond). Each started MiB copied costs one card's worth of the daily clone allowance. |
| `.apkg` import | Unchanged: Anki notes still map to Basic or Cloze. Mapping Anki models to custom types is future work. | Unchanged. Anki's own Image Occlusion notes are a cloze type, so they still import as Cloze text. |
| `.apkg` export (future) | Map to an Anki model with the same fields and templates; the ordinals give the card order. | Map to Anki's Image Occlusion note type, or skip with a report entry. |
| AI drafts | Not produced: drafts stay Basic or Cloze, and the output schema allows only those. | Not produced. |
| AI evaluation | Graded against all the note's fields, as Basic and Cloze are. | Refused (422): the answer is a region of an image the model never sees. |
| Mock exams and summaries | Use all fields as source text. | Use only the text fields (`header`, `back_extra`). |
| Card browser | The preview is the first field in the type's order; text search covers every field. | The preview is the header; text search covers the text fields. |
| Bulk actions | Unchanged: move, delete and tag act on notes. | Unchanged. |
| Account export | Includes the note types, and each note's `note_type_id`. | Includes each note's `occlusion`. The image appears in the media list. |
| Account deletion | Note types are deleted after the notes that use them. | Images go with the rest of the account's media. |

### Concurrency

- Creating or editing a custom note locks its type first, then the note. Editing or deleting a type locks the type, then its notes. Locks are always taken in that order, so a note edit and a type edit never deadlock, and a type edit never misses a note created meanwhile.
- The per-owner type limit is checked by count without a lock, so concurrent creations can pass it by a few. It is a resource bound, not a security boundary.

### Validation errors

- Validation messages name positions ("template 2", "field 3") rather than echoing names or content back (ADR 0011).

## Consequences

- A type edit re-derives every note of the type in one request, so its cost grows with the number of notes. That is acceptable at the sizes the clone and import limits allow (20,000 notes), but a type with very many notes makes a slow edit.
- Checking an occlusion image loads the media row with its bytes. A metadata-only lookup can replace it if image notes become frequent.
- The template subset is smaller than Anki's. Anki types that use conditionals or filters cannot be recreated exactly yet, and importing them as custom types needs those features first.
- Cloned images are copies, so a clone uses storage in proportion to the images it copies. That is bounded per clone and by the daily clone allowance, which counts the copied bytes. There is no per-owner storage quota yet.
- Locking a note re-reads its row, so an edit never writes back a copy read before the lock was taken.
- Notes reference images by id without a foreign key. Media is never deleted on its own, only with the whole account, so a reference cannot dangle today. A media delete endpoint would need to refuse images that notes still use.
