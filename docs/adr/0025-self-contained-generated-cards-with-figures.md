# ADR 0025: Self-contained generated cards, with the figures they need

- **Status:** Accepted
- **Date:** 2026-11-10
- **Amended by:** ADR 0026 (measured figures on scanned pages; a model's own box is not cropped on a scan)

## Context

A card is studied alone, never beside the page it came from. Generated cards could still lean on that page:

- They named its labels: "system (c) on the worksheet", "as shown in the document", "the instruction at the top of the worksheet".
- They described a graph the student could not see: "the graph shows two parallel lines".

Three things caused this:

- The prompt asked for self-contained questions only in passing.
- A born-digital page with text was sent as text alone, so the model never saw its graphs.
- A draft card was text only, so even a scanned page the model did see could not pass a graph on to the card.

## Decision

### The prompt

Every card must make sense to a student who has never seen the document:

- It writes out the equations, values, code and definitions it needs.
- It never refers to the document, its pages, sections, exercises or labels.
- No cards are made about the document itself (its title, instructions or layout).

A page with nothing to study may now return no cards, and that is not a failure.

### The model sees the page's graphics

The PDF extractor (`adapters/extraction.graphic_boxes`) decides whether a text page has graphics.

**What counts:**
- Embedded images and clusters of vector drawings that take up a real share of the page.

**What does not count:**
- Rules, underlines and small marks.
- Frames and tables around dense text (measured by how much of the box words cover).
- Page-wide fills.
- Blurry page-wide background pictures (slides).

A page-covering image of scan resolution (100 dpi or more) means the page is a scan.

A text page with graphics is sent as its text and then its rendered image. On a sample of 871 text pages, 119 qualify, and each of those has a figure.

### Cards can show a figure

The draft schema gains:
- `figures`: boxes in thousandths of the page, from its top-left corner.
- `figure` on each card: the 1-based figure it shows, or 0 for none.

For each figure a card shows, the worker:
1. Snaps the model's box to the PDF's own figure box when they overlap (intersection over union of at least 0.5). Otherwise it pads the box by 4% of the page on each side.
2. Crops the figure from the page image.
3. Stores the crop as the job owner's media, in the same transaction as the page's drafts.
4. Puts `![Figure](media:<id>)` first on the card's question side.

The web renderer maps `media:<id>` to the API's media URL, so stored text does not depend on where the API is mounted. Library members can read an admin's media already (ADR 0024).

**Figures are only an aid.** None of these fails a page; the affected card keeps its text:
- A box that is not a rectangle within the page.
- A figure past the twelfth.
- A card naming a missing figure.
- A figure that cannot be cropped.

**Cropping untrusted images is bounded.** The cropper:
- Opens only PNG or JPEG, as exactly that type.
- Refuses an image whose header claims more than `MAX_RASTER_PIXELS`, before decoding it.
- Leaves out a crop over the media size limit.

**Cloning a shared deck** copies the figures its cards show, within the clone's media byte cap, and points the copies' text at them. A reference to media the link owner does not own is left as written.

### Truncated output

An answer cut off at the output limit is unusable model output (`OutputTruncated`, a kind of `InvalidModelOutput`), not an unavailable provider. The usage the provider reported is recorded as spend before the page fails.

The generation output limit rises from 4096 to 16,000 tokens. On OpenAI's reasoning models the hidden reasoning counts against the same limit.

## Consequences

- Pages with figures cost more: an image is sent alongside the text, and figures are stored as media.
- Figures from drafts that are never approved stay in the owner's media.
- A share-link preview shows a figure only to a reader who can read the owner's media.
- Exports carry the `media:` reference as text.
- A model's box on a scanned page is approximate, and the padding keeps labels in view. A badly placed box crops the wrong part of the page. The card still shows its source page reference.
