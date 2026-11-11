# ADR 0026: Measured figures on scanned pages

- **Status:** Accepted
- **Date:** 2026-11-10
- **Amends:** ADR 0025

## Context

ADR 0025 crops a figure from the box the model gives, unless the box matches a figure found in a born-digital PDF's own drawings or images. A scanned or photographed page has neither, so its crops came from the model's box alone.

On a phone-scanned worksheet with eleven graphs, those boxes were not good enough:

- **They were often far off.** Some missed the graph by a fifth of the page or more. One boxed y 190 to 760 for a graph at 650 to 1000.
- **Other ways of asking did not fix it.** A larger model, ruler lines drawn on the page image, and a second pass on a zoomed crop were all tried. The best still cut the edge off most graphs.
- **Some boxes covered most of the page.** They would have shown the student's worked answers on the card's question side.

The model also attached few figures at all. It described graphs in words ("the top-right graph") and showed empty grids the student never filled in.

## Decision

### Ruled figures are measured in the page image

`adapters/ruled_figures` finds figures on a page image by their lines. A graph's grid, axes and frame are long straight lines running both across and down, and text never makes those.

The page is measured like this:

1. Render the page at 500 pixels wide in grayscale, and never at more than 500,000 pixels. A page's size comes from the untrusted file, so a tall, thin page is rendered smaller instead of in the hundreds of millions of pixels.
2. Keep the dark pixels that lie on long straight runs, across or down. A run may skip small gaps, so dotted grid lines count. "Dark" is relative to the paper: darker than 215 or than 35 shades below the page's median, whichever is darker, so a dim photo of gray paper is not all ink.
3. Group the kept pixels.
4. Split a group where only a thin line crosses a wide band of it. This separates two graphs joined by one graph's axis. A split happens only when both pieces would be figure-sized, so the gaps in a faint grid never cut a graph apart.
5. Keep a group as a figure when it spans at least 15% of the page's width and 10% of its height, with at least three horizontal and three vertical lines.
6. Drop a figure that covers more than half the page. A dark desk around a photographed page, or a scanner's shadow, is the page, not a figure.

The first twelve figures are kept, as many as a page's output may list. If measuring fails, the page simply has no measured figures.

This runs on three kinds of page:

- PDF pages without text.
- PDF scans under a text layer.
- Uploaded PNG or JPEG pages, after their pixel size is checked against `MAX_RASTER_PIXELS`. An upload that fails the check is still sent to the model, just without measured figures.

On the worksheet, it found all 13 graphs on its 23 pages, at about 0.1 seconds a page. The framed definitions it also finds are harmless: they are only candidates.

### Each measured box carries its own margin

The extractor pads each figure it finds. `snap` then uses a matched box as it is:

- **Drawings and images from a born-digital PDF** get 20 thousandths, for labels set beside the drawing.
- **Ruled figures** get 6, because their lines already reach the axis labels. On a worksheet, an answer is often written just below the graph.

### The model chooses among measured boxes

The generation prompt lists the page's measured boxes and asks the model to use exactly one of them when a card shows that figure. In testing, the model copied the listed box on every page that had one. The crop is then exact. Born-digital pages with drawings or images list their boxes the same way.

When nothing matches, the model's own box is used with the full margin. If that box covers more than half the page, the figure is left out and the card keeps its text.

### Boxes that run off the page are clipped

A box edge outside 0 to 1000 is clipped to the page instead of the whole figure being dropped.

### The prompt asks for figure cards

The prompt now asks the model to:

- Make at least one card that shows each graph, diagram or chart worth studying, and asks the student to read or interpret it.
- Call it "the graph shown", never by where it sits on the page.
- Never show a blank or unfinished figure.
- Keep the question text and worked solutions out of a figure's box.

## Consequences

- A figure drawn without straight lines on a scanned page (a photo, or a freehand sketch) is not measured. Its crop still comes from the model's box.
- A ruled figure that holds the student's own working (labels, plotted points) shows that working. The measure cannot tell printed lines from handwriting on them.
- Measuring is plain Python on the PyMuPDF worker thread. It adds about 0.1 seconds to each scanned page.
- A large figure that is not measured, such as a full-page diagram on a text-less page or a photo of a whiteboard, is no longer shown if the model's box covers more than half the page. Only that figure is lost. On worksheets, a box that size was the page with its answers.
- A card whose figure is left out keeps its text, which may still say "the graph shown". The owner reviews every draft before it joins a deck.
