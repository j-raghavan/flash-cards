// SPDX-License-Identifier: AGPL-3.0-only
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { NoteTypeDef } from "../api/types";
import { USER, mockApi, renderApp } from "../test/helpers";
import { toInput } from "./NoteTypesPage";

const me = { "GET /auth/me": { body: USER } };

const VOCAB: NoteTypeDef = {
  id: "nt1",
  name: "Vocab",
  fields: ["Word", "Meaning"],
  templates: [{ ordinal: 0, name: "Recognize", front: "{{Word}}", back: "{{FrontSide}} = {{Meaning}}" }],
  created_at: "2026-01-01T00:00:00Z",
};

describe("toInput", () => {
  it("lists renamed saved fields and keeps template ordinals", () => {
    expect(
      toInput(
        " Vocab ",
        [
          { key: 1, original: "Word", name: "Term " },
          { key: 2, original: "Meaning", name: "Meaning" },
          { key: 3, original: null, name: "Example" },
        ],
        [
          { key: 4, ordinal: 0, name: "A", front: "{{Term}}", back: "" },
          { key: 5, name: "B", front: "{{Meaning}}", back: "" },
        ],
      ),
    ).toEqual({
      name: "Vocab",
      fields: ["Term", "Meaning", "Example"],
      templates: [
        { ordinal: 0, name: "A", front: "{{Term}}", back: "" },
        { name: "B", front: "{{Meaning}}", back: "" },
      ],
      renamed: { Word: "Term" },
    });
  });
});

describe("note types", () => {
  it("lists note types and creates one with fields, templates and a preview", async () => {
    const calls = mockApi({
      ...me,
      "GET /note-types": { body: [VOCAB, { ...VOCAB, id: "nt2", name: "One", fields: ["A"], templates: [VOCAB.templates[0], VOCAB.templates[0]] }] },
      "POST /note-types": { status: 201, body: { ...VOCAB, id: "nt3", name: "Capitals" } },
      "GET /note-types/nt3": { body: { ...VOCAB, id: "nt3", name: "Capitals" } },
    });
    renderApp("/note-types");
    const user = userEvent.setup();
    expect(await screen.findByRole("link", { name: "Vocab" })).toHaveAttribute("href", "/note-types/nt1");
    expect(screen.getByText("2 fields, 1 card per note")).toBeInTheDocument();
    expect(screen.getByText("1 field, 2 cards per note")).toBeInTheDocument();
    expect(document.title).toBe("Note types · Flash-Cards");
    await user.click(screen.getByRole("link", { name: "New note type" }));

    const create = await screen.findByRole("button", { name: "Create note type" });
    expect(create).toBeDisabled();
    await user.type(screen.getByLabelText("Name"), "Capitals");
    expect(create).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Add a field" }));
    expect(create).toBeDisabled(); // an empty field name
    await user.type(screen.getByLabelText("Field 3"), "Front");
    expect(screen.getByText("Each field needs its own name.")).toBeInTheDocument();
    await user.clear(screen.getByLabelText("Field 3"));
    await user.type(screen.getByLabelText("Field 3"), "Region");
    await user.click(screen.getByRole("button", { name: "Remove field 3" }));
    const preview = screen.getByRole("complementary", { name: "Preview" });
    expect(within(preview).getAllByText("Front")[0]?.tagName).toBe("EM");

    await user.click(screen.getByRole("button", { name: "Add a card" }));
    const second = screen.getByRole("group", { name: "Card 2" });
    expect(within(second).getByLabelText("Card name")).toHaveValue("Card 2");
    await user.type(within(second).getByLabelText("Front template"), "{{{{Back}}");
    await user.type(within(second).getByLabelText("Back template"), " more");
    await user.click(within(second).getByRole("button", { name: "Remove card 2" }));
    await user.click(screen.getByRole("button", { name: "Add a card" }));
    const again = screen.getByRole("group", { name: "Card 2" });
    await user.type(within(again).getByLabelText("Front template"), "{{{{Back}}");
    await user.clear(within(again).getByLabelText("Card name"));
    await user.type(within(again).getByLabelText("Card name"), "Reverse");
    await user.click(create);
    expect(await screen.findByRole("heading", { name: "Capitals" })).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({
      name: "Capitals",
      fields: ["Front", "Back"],
      templates: [
        { name: "Card 1", front: "{{Front}}", back: "{{FrontSide}}\n\n---\n\n{{Back}}" },
        { name: "Reverse", front: "{{Back}}", back: "{{FrontSide}}" },
      ],
    });
  });

  it("edits a note type, renaming a field, and deletes it", async () => {
    const calls = mockApi({
      ...me,
      "GET /note-types/nt1": { body: VOCAB },
      "PATCH /note-types/nt1": (call) => ({ body: { ...VOCAB, ...(call.body as object) } }),
      "DELETE /note-types/nt1": { status: 204 },
      "GET /note-types": { body: [] },
    });
    renderApp("/note-types/nt1");
    const user = userEvent.setup();
    const word = await screen.findByLabelText("Field 1");
    expect(document.title).toBe("Vocab · Flash-Cards");
    expect(screen.getByText(/Renaming a field keeps its contents/)).toBeInTheDocument();
    await user.clear(word);
    await user.type(word, "Term");
    await user.click(screen.getByRole("button", { name: "Save note type" }));
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({
      name: "Vocab",
      fields: ["Term", "Meaning"],
      templates: [{ ordinal: 0, name: "Recognize", front: "{{Word}}", back: "{{FrontSide}} = {{Meaning}}" }],
      renamed: { Word: "Term" },
    });
    // The form starts again from what was saved: no rename is sent twice.
    await user.click(screen.getByRole("button", { name: "Save note type" }));
    await waitFor(() => {
      expect(calls.filter((c) => c.method === "PATCH")[1]?.body).toMatchObject({ renamed: {} });
    });
    await user.click(screen.getByRole("button", { name: "Delete note type" }));
    await user.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete note type" }));
    expect(await screen.findByText("No note types yet.")).toBeInTheDocument();
  });

  it("reports a note type in use, one that cannot load, a list that cannot load and a failed save", async () => {
    mockApi({
      ...me,
      "GET /note-types/nt1": { body: VOCAB },
      "PATCH /note-types/nt1": { status: 422, body: { detail: "A template references an unknown field." } },
      "DELETE /note-types/nt1": { status: 409, body: { detail: "Notes still use this note type." } },
    });
    const first = renderApp("/note-types/nt1");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Save note type" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("A template references an unknown field.");
    await user.click(screen.getByRole("button", { name: "Delete note type" }));
    await user.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete note type" }));
    await waitFor(() => {
      expect(screen.getAllByRole("alert")).toHaveLength(2);
    });
    expect(screen.getAllByRole("alert")[1]).toHaveTextContent("Notes still use this note type.");
    first.unmount();
    mockApi({ ...me, "GET /note-types/nt9": { status: 404, body: { detail: "Note type not found." } } });
    const second = renderApp("/note-types/nt9");
    expect(await screen.findByRole("alert")).toHaveTextContent("Note type not found.");
    second.unmount();
    mockApi({ ...me, "GET /note-types": { status: 500 } });
    renderApp("/note-types");
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong");
  });

  it("is reached from the deck list", async () => {
    mockApi({ ...me, "GET /decks": { body: [] } });
    renderApp("/");
    expect(await screen.findByRole("link", { name: "Custom note types" })).toHaveAttribute("href", "/note-types");
  });
});
