// SPDX-License-Identifier: AGPL-3.0-only
import { fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { Note } from "../api/types";
import { USER, mockApi, renderApp } from "../test/helpers";

const me = { "GET /auth/me": { body: USER } };

const note = (over: Partial<Note> = {}): Note => ({
  id: "n1",
  deck_id: "d1",
  note_type: "basic",
  fields: { front: "Old front", back: "Old back", source: "slides.pdf p3" },
  tags: ["bio", "ch::1"],
  created_at: "2026-01-01T00:00:00Z",
  cards: [{ id: "c1", deck_id: "d1", note_id: "n1", ordinal: 0, suspended: false, flag: 0, buried_until: null }],
  ...over,
});

describe("new card", () => {
  it("adds a basic card with tags and previews it", async () => {
    const calls = mockApi({ ...me, "POST /decks/d1/notes": { status: 201, body: [{ id: "c1" }] } });
    renderApp("/decks/d1/notes/new");
    const user = userEvent.setup();
    const add = await screen.findByRole("button", { name: "Add card" });
    expect(add).toBeDisabled();
    await user.type(screen.getByLabelText("Front"), "Define **osmosis**");
    await user.type(screen.getByLabelText("Back"), "Water moving");
    await user.type(screen.getByLabelText("Tags"), "bio, ch::1 bio");
    expect(within(screen.getByRole("complementary", { name: "Preview" })).getByText("osmosis").tagName).toBe("STRONG");
    await user.click(add);
    expect(await screen.findByText("Added 1 card.")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({
      note_type: "basic",
      fields: { front: "Define **osmosis**", back: "Water moving" },
      tags: ["bio", "ch::1"],
    });
    expect(screen.getByLabelText("Front")).toHaveValue("");
    expect(screen.getByLabelText("Tags")).toHaveValue("bio, ch::1 bio");
  });

  it("builds a cloze note with the deletion helper and saves with Ctrl+Enter", async () => {
    const calls = mockApi({ ...me, "POST /decks/d1/notes": { status: 201, body: [{ id: "c1" }, { id: "c2" }] } });
    renderApp("/decks/d1/notes/new");
    const user = userEvent.setup();
    await user.click(await screen.findByLabelText("Cloze"));
    expect(screen.getByText("No deletions yet.")).toBeInTheDocument();
    const text = screen.getByLabelText("Text");
    await user.type(text, "Paris is in France");
    (text as HTMLTextAreaElement).setSelectionRange(0, 5);
    await user.click(screen.getByRole("button", { name: "Hide selection as c1" }));
    expect(text).toHaveValue("{{c1::Paris}} is in France");
    (text as HTMLTextAreaElement).setSelectionRange(text.textContent.length + 99, text.textContent.length + 99);
    fireEvent.select(text);
    await user.click(screen.getByRole("button", { name: "Hide selection as c2" }));
    expect((text as HTMLTextAreaElement).value).toContain("{{c2::answer}}");
    expect(screen.getByText("2 cards from this note.")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Extra"), "Capital");
    await user.keyboard("{Control>}{Enter}{/Control}");
    expect(await screen.findByText("Added 2 cards.")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST")?.body).toMatchObject({
      note_type: "cloze",
      fields: { extra: "Capital" },
    });
  });

  it("does not save an incomplete note from the keyboard, and shows server errors", async () => {
    const calls = mockApi({ ...me, "POST /decks/d1/notes": { status: 422, body: { detail: "a tag is invalid" } } });
    renderApp("/decks/d1/notes/new");
    const user = userEvent.setup();
    const front = await screen.findByLabelText("Front");
    await user.click(front);
    await user.keyboard("{Meta>}{Enter}{/Meta}");
    expect(calls.some((c) => c.method === "POST")).toBe(false);
    await user.type(front, "F");
    await user.type(screen.getByLabelText("Back"), "B");
    await user.keyboard("{Enter}");
    await user.click(screen.getByRole("button", { name: "Add card" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("a tag is invalid");
  });
});

describe("edit card", () => {
  it("saves changes and keeps fields the editor does not show", async () => {
    const calls = mockApi({ ...me, "GET /notes/n1": { body: note() }, "PATCH /notes/n1": { body: note() } });
    renderApp("/notes/n1/edit");
    const user = userEvent.setup();
    const front = await screen.findByLabelText("Front");
    expect(front).toHaveValue("Old front");
    expect(screen.getByLabelText("Tags")).toHaveValue("bio ch::1");
    expect(screen.getByLabelText("Basic")).toBeDisabled();
    await user.clear(front);
    await user.type(front, "New front");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({
      fields: { front: "New front", back: "Old back", source: "slides.pdf p3" },
      tags: ["bio", "ch::1"],
    });
  });

  it("edits a cloze note", async () => {
    mockApi({
      ...me,
      "GET /notes/n1": { body: note({ note_type: "cloze", fields: { text: "{{c1::x}}", extra: "e" } }) },
    });
    renderApp("/notes/n1/edit");
    expect(await screen.findByLabelText("Text")).toHaveValue("{{c1::x}}");
    expect(screen.getByLabelText("Extra")).toHaveValue("e");
  });

  it("deletes a note after confirming, and reports a failure", async () => {
    let fail = true;
    mockApi({
      ...me,
      "GET /notes/n1": { body: note() },
      "DELETE /notes/n1": () => (fail ? { status: 500 } : { status: 204 }),
      "GET /decks": { body: [] },
      "GET /decks/d1/stats": { status: 404, body: { detail: "Not found." } },
    });
    renderApp("/notes/n1/edit");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Delete note" }));
    expect(screen.getByRole("alertdialog")).toHaveTextContent("its 1 card and their review history");
    await user.click(screen.getByRole("button", { name: "Delete for good" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong");
    fail = false;
    await user.click(screen.getByRole("button", { name: "Delete note" }));
    await user.click(screen.getByRole("button", { name: "Delete for good" }));
    expect(await screen.findByRole("heading", { name: "Deck not found" })).toBeInTheDocument();
  });

  it("edits the fields of custom and image occlusion notes as plain fields", async () => {
    const calls = mockApi({
      ...me,
      "GET /notes/n1": { body: note({ note_type: "custom", fields: { Word: "chat", Meaning: "" } }) },
      "PATCH /notes/n1": { body: note() },
    });
    const { unmount } = renderApp("/notes/n1/edit");
    const user = userEvent.setup();
    const meaning = await screen.findByLabelText("Meaning");
    expect(screen.queryByRole("radio", { name: "Basic" })).not.toBeInTheDocument();
    expect(screen.queryByRole("complementary", { name: "Preview" })).not.toBeInTheDocument();
    await user.clear(screen.getByLabelText("Word"));
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
    await user.type(meaning, "cat");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({
      fields: { Word: "", Meaning: "cat" },
      tags: ["bio", "ch::1"],
    });
    unmount();
    mockApi({
      ...me,
      "GET /notes/n1": { body: note({ note_type: "image_occlusion", fields: { header: "Heart", back_extra: "" } }) },
    });
    renderApp("/notes/n1/edit");
    expect(await screen.findByLabelText("Header")).toHaveValue("Heart");
    expect(screen.getByLabelText("Back extra")).toHaveValue("");
    expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled();
    expect(screen.queryByLabelText("Front")).not.toBeInTheDocument();
  });

  it("shows a note that cannot load", async () => {
    mockApi({ ...me, "GET /notes/n1": { status: 404, body: { detail: "Not found." } } });
    renderApp("/notes/n1/edit");
    expect(await screen.findByRole("alert")).toHaveTextContent("Not found.");
  });
});
