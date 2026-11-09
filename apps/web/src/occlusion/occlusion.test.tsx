// SPDX-License-Identifier: AGPL-3.0-only
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import type { Occlusion } from "../api/types";
import { USER, mockApi, renderApp } from "../test/helpers";
import { MAX_MASKS, OcclusionEditor, fitMask } from "./OcclusionEditor";

const me = { "GET /auth/me": { body: USER } };

function Harness({ initial = null }: { initial?: Occlusion | null }) {
  const [occlusion, setOcclusion] = useState<Occlusion | null>(initial);
  return (
    <>
      <OcclusionEditor occlusion={occlusion} onChange={setOcclusion} />
      <output data-testid="value">{JSON.stringify(occlusion)}</output>
    </>
  );
}

function value(): Occlusion {
  return JSON.parse(screen.getByTestId("value").textContent) as Occlusion;
}

const png = (size = 4): File => new File(["x".repeat(size)], "heart.png", { type: "image/png" });

/** Give the drawing surface a 200 by 100 pixel box at the origin. */
function sizeSurface(): HTMLElement {
  const surface = document.querySelector(".occlusion-surface") as HTMLElement;
  surface.getBoundingClientRect = () => ({ left: 0, top: 0, width: 200, height: 100 }) as DOMRect;
  surface.setPointerCapture = vi.fn();
  return surface;
}

describe("fitMask", () => {
  it("keeps a mask inside the image with a whole card number", () => {
    expect(fitMask({ id: 2.6, left: 0.9, top: -1, width: 0.5, height: 2 })).toEqual({
      id: 3,
      left: 0.5,
      top: 0,
      width: 0.5,
      height: 1,
    });
    expect(fitMask({ id: 0, left: 0, top: 0, width: 0, height: 0.001 })).toEqual({
      id: 1,
      left: 0,
      top: 0,
      width: 0.01,
      height: 0.01,
    });
  });
});

describe("occlusion editor", () => {
  it("uploads an image, draws masks by dragging and edits them by hand", async () => {
    const calls = mockApi({ "POST /media": { status: 201, body: { id: "m1", content_type: "image/png" } } });
    render(<Harness />);
    const user = userEvent.setup();
    expect(screen.queryByRole("button", { name: "Add a mask" })).not.toBeInTheDocument();
    await user.upload(screen.getByLabelText("Image"), png());
    expect(await screen.findByRole("img", { name: "The image to hide parts of" })).toHaveAttribute(
      "src",
      "/api/media/m1",
    );
    expect(calls[0]?.method).toBe("POST");
    expect(screen.getByText("No masks yet.")).toBeInTheDocument();

    const surface = sizeSurface();
    fireEvent.pointerDown(surface, { button: 2, clientX: 10, clientY: 10, pointerId: 1 });
    fireEvent.pointerMove(surface, { clientX: 90, clientY: 90 });
    fireEvent.pointerUp(surface);
    expect(value().masks).toEqual([]);
    fireEvent.pointerDown(surface, { button: 0, clientX: 50, clientY: 25, pointerId: 1 });
    fireEvent.pointerMove(surface, { clientX: 150, clientY: 75 });
    expect(document.querySelectorAll(".mask")).toHaveLength(1); // the draft
    fireEvent.pointerUp(surface);
    expect(value().masks).toEqual([{ id: 1, left: 0.25, top: 0.25, width: 0.5, height: 0.5 }]);
    // Dragging up and left works too; a click without a drag adds nothing.
    fireEvent.pointerDown(surface, { button: 0, clientX: 300, clientY: 100, pointerId: 1 });
    fireEvent.pointerMove(surface, { clientX: 100, clientY: 50 });
    fireEvent.pointerUp(surface);
    fireEvent.pointerDown(surface, { button: 0, clientX: 50, clientY: 50, pointerId: 1 });
    fireEvent.pointerUp(surface);
    fireEvent.pointerDown(surface, { button: 0, clientX: 50, clientY: 50, pointerId: 1 });
    fireEvent.pointerCancel(surface);
    fireEvent.pointerUp(surface);
    fireEvent.pointerMove(surface, { clientX: 60, clientY: 60 });
    expect(value().masks).toHaveLength(2);
    expect(value().masks[1]).toEqual({ id: 2, left: 0.5, top: 0.5, width: 0.5, height: 0.5 });
    expect(screen.getByText("2 cards from 2 masks.")).toBeInTheDocument();

    const second = screen.getByRole("group", { name: "Mask 2" });
    const card = within(second).getByLabelText("Card");
    // Typing is kept as typed until the field is left or Enter is pressed.
    await user.clear(card);
    expect(card).toHaveValue(null);
    await user.type(card, "15");
    expect(value().masks[1]?.id).toBe(2);
    await user.keyboard("{Enter}");
    expect(value().masks[1]?.id).toBe(15);
    await user.clear(card);
    await user.type(card, "1");
    await user.tab();
    expect(screen.getByText("1 card from 2 masks.")).toBeInTheDocument();
    const width = within(second).getByLabelText("Width (%)");
    await user.clear(width);
    await user.tab(); // an empty field changes nothing
    expect(width).toHaveValue(50);
    await user.clear(width);
    await user.type(width, "25{Enter}");
    expect(value().masks[1]).toMatchObject({ id: 1, width: 0.25, left: 0.5 });
    await user.type(width, "{Backspace}{Backspace}30");
    await user.keyboard("a"); // other keys type as usual
    await user.tab();
    expect(value().masks[1]?.width).toBeCloseTo(0.3);
    within(second).getByLabelText("Left (%)").focus();
    expect(second.closest("li")).toHaveClass("selected");

    await user.click(screen.getByRole("button", { name: "Add a mask" }));
    expect(value().masks[2]).toEqual({ id: 2, left: 0.4, top: 0.45, width: 0.2, height: 0.1 });
    await user.click(within(screen.getByRole("group", { name: "Mask 1" })).getByRole("button", { name: "Remove" }));
    expect(value().masks.map((m) => m.id)).toEqual([1, 2]);
  });

  it("keeps the masks when the image is replaced, and refuses files it cannot use", async () => {
    mockApi({ "POST /media": { status: 201, body: { id: "m2", content_type: "image/png" } } });
    render(<Harness initial={{ image_id: "m1", masks: [{ id: 1, left: 0, top: 0, width: 0.5, height: 0.5 }] }} />);
    const user = userEvent.setup({ applyAccept: false });
    const input = screen.getByLabelText("Replace the image");
    await user.upload(input, new File(["%PDF"], "notes.pdf", { type: "application/pdf" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Choose a PNG, JPEG, GIF or WebP image.");
    const big = png();
    Object.defineProperty(big, "size", { value: 26 * 1024 * 1024 });
    await user.upload(input, big);
    expect(screen.getByRole("alert")).toHaveTextContent("larger than 25 MB");
    fireEvent.change(input, { target: { files: [] } });
    await user.upload(input, png());
    await waitFor(() => {
      expect(value().image_id).toBe("m2");
    });
    expect(value().masks).toHaveLength(1);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("stops at the mask limit", () => {
    const masks = Array.from({ length: MAX_MASKS }, (_, i) => ({ id: i + 1, left: 0, top: 0, width: 0.1, height: 0.1 }));
    render(<Harness initial={{ image_id: "m1", masks }} />);
    expect(screen.getByRole("button", { name: "Add a mask" })).toBeDisabled();
    expect(screen.getByText(/At most 100 masks/)).toBeInTheDocument();
    const surface = sizeSurface();
    fireEvent.pointerDown(surface, { button: 0, clientX: 10, clientY: 10, pointerId: 1 });
    fireEvent.pointerMove(surface, { clientX: 90, clientY: 90 });
    fireEvent.pointerUp(surface);
    expect(value().masks).toHaveLength(MAX_MASKS);
  });

  it("shows an upload the server refuses", async () => {
    mockApi({ "POST /media": { status: 413, body: { detail: "File too large." } } });
    render(<Harness />);
    await userEvent.setup().upload(screen.getByLabelText("Image"), png());
    expect(await screen.findByRole("alert")).toHaveTextContent("File too large.");
  });
});

describe("new image occlusion and custom notes", () => {
  it("adds an image occlusion note with its masks", async () => {
    const calls = mockApi({
      ...me,
      "POST /media": { status: 201, body: { id: "m1", content_type: "image/png" } },
      "POST /decks/d1/notes": { status: 201, body: [{ id: "c1" }, { id: "c2" }] },
    });
    renderApp("/decks/d1/notes/new");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("radio", { name: "Image occlusion" }));
    const add = screen.getByRole("button", { name: "Add card" });
    await user.type(screen.getByLabelText("Header"), "Label the heart");
    await user.upload(await screen.findByLabelText("Image"), png());
    await screen.findByRole("img", { name: "The image to hide parts of" });
    expect(add).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Add a mask" }));
    await user.click(screen.getByRole("button", { name: "Add a mask" }));
    await user.click(add);
    expect(await screen.findByText("Added 2 cards.")).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/decks/d1/notes")?.body).toEqual({
      note_type: "image_occlusion",
      fields: { header: "Label the heart", back_extra: "" },
      tags: [],
      occlusion: {
        image_id: "m1",
        masks: [
          { id: 1, left: 0.4, top: 0.45, width: 0.2, height: 0.1 },
          { id: 2, left: 0.4, top: 0.45, width: 0.2, height: 0.1 },
        ],
      },
    });
    expect(screen.getByLabelText("Header")).toHaveValue("");
    expect(screen.getByLabelText("Image")).toBeInTheDocument();
  });

  it("adds a note of a custom type with a preview from its first template", async () => {
    const calls = mockApi({
      ...me,
      "GET /note-types": {
        body: [
          {
            id: "nt1",
            name: "Vocab",
            fields: ["Word", "Meaning"],
            templates: [{ ordinal: 0, name: "Recognize", front: "**{{Word}}**", back: "{{FrontSide}} = {{Meaning}}" }],
            created_at: "2026-01-01T00:00:00Z",
          },
        ],
      },
      "POST /decks/d1/notes": { status: 201, body: [{ id: "c1" }] },
    });
    renderApp("/decks/d1/notes/new");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("radio", { name: "Custom" }));
    await user.selectOptions(await screen.findByLabelText("Note type"), "nt1");
    await user.type(screen.getByLabelText("Word"), "gato");
    await user.type(screen.getByLabelText("Meaning"), "cat");
    const preview = screen.getByRole("complementary", { name: "Preview" });
    expect(within(preview).getAllByText("gato")[0]?.tagName).toBe("STRONG");
    await user.click(screen.getByRole("button", { name: "Add card" }));
    expect(await screen.findByText("Added 1 card.")).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/decks/d1/notes")?.body).toEqual({
      note_type: "custom",
      fields: { Word: "gato", Meaning: "cat" },
      tags: [],
      note_type_id: "nt1",
    });
  });

  it("points to the note type editor when there are none, and shows a list that cannot load", async () => {
    mockApi({ ...me, "GET /note-types": { body: [] } });
    const { unmount } = renderApp("/decks/d1/notes/new");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("radio", { name: "Custom" }));
    expect(await screen.findByRole("link", { name: "Create one" })).toHaveAttribute("href", "/note-types/new");
    expect(screen.getByRole("button", { name: "Add card" })).toBeDisabled();
    unmount();
    mockApi({ ...me, "GET /note-types": { status: 500 } });
    renderApp("/decks/d1/notes/new");
    await user.click(await screen.findByRole("radio", { name: "Custom" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong");
  });
});
