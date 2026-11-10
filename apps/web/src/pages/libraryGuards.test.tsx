// SPDX-License-Identifier: AGPL-3.0-only
// Owner-only pages reached by a typed or old link to library material (ADR 0024).
import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { BrowserRow, Deck, Note } from "../api/types";
import { USER, mockApi, renderApp } from "../test/helpers";

const me = { "GET /auth/me": { body: USER } };

const libraryDeck: Deck = {
  id: "l1",
  name: "Anatomy",
  parent_id: null,
  created_at: "2026-01-01T00:00:00Z",
  owned: false,
};
const ownDeck: Deck = { ...libraryDeck, id: "d1", name: "Biology", owned: true };

const NOTICE = "This deck is in the library; only its owner can change it.";

async function expectNotice(heading: string): Promise<void> {
  expect(await screen.findByRole("heading", { name: heading, level: 1 })).toBeInTheDocument();
  expect(screen.getByText(NOTICE)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Study it" })).toHaveAttribute("href", "/decks/l1/study");
  expect(screen.getByRole("link", { name: "Back to the deck" })).toHaveAttribute("href", "/decks/l1");
  await waitFor(() => {
    expect(document.title).toBe(`${heading} · Flash-Cards`);
  });
}

describe("library guards", () => {
  for (const [path, heading] of [
    ["/decks/l1/notes/new", "Add a card"],
    ["/decks/l1/generate", "Generate cards"],
    ["/decks/l1/share", "Share deck"],
    ["/decks/l1/exam", "Mock exam"],
  ] as const) {
    it(`shows a read-only notice instead of ${path}`, async () => {
      const calls = mockApi({ ...me, "GET /decks/l1": { body: libraryDeck } });
      renderApp(path);
      await expectNotice(heading);
      expect(screen.queryByRole("form")).toBeNull();
      expect(screen.queryByRole("textbox")).toBeNull();
      // The page itself never loads, so nothing else is asked of the server.
      expect(calls.filter((c) => c.path !== "/auth/me").map((c) => c.path)).toEqual(["/decks/l1"]);
    });
  }

  it("keeps the page for the user's own deck", async () => {
    mockApi({ ...me, "GET /decks/d1": { body: ownDeck } });
    renderApp("/decks/d1/notes/new");
    expect(await screen.findByRole("heading", { name: "Add a card" })).toBeInTheDocument();
    expect(screen.queryByText(NOTICE)).toBeNull();
    expect(screen.getByLabelText("Front")).toBeInTheDocument();
  });

  it("shows a read-only notice instead of editing a library note", async () => {
    const note: Note = {
      id: "n1",
      deck_id: "l1",
      note_type: "basic",
      fields: { front: "Q", back: "A" },
      tags: [],
      created_at: "2026-01-01T00:00:00Z",
      cards: [],
      owned: false,
    };
    mockApi({ ...me, "GET /notes/n1": { body: note } });
    renderApp("/notes/n1/edit");
    await expectNotice("Edit card");
    expect(screen.queryByRole("button", { name: "Delete note" })).toBeNull();
    expect(screen.queryByLabelText("Front")).toBeNull();
  });

  it("says when recent generations cannot load, and still offers the upload", async () => {
    mockApi({ ...me, "GET /decks/d1": { body: ownDeck }, "GET /ai/jobs": { status: 500 } });
    renderApp("/decks/d1/generate");
    expect(await screen.findByText("Recent generations could not be loaded.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Generate drafts" })).toBeInTheDocument();
  });
});

describe("browser deck filter", () => {
  const row: BrowserRow = {
    card_id: "c1",
    note_id: "n1",
    deck_id: "d1",
    deck_name: "Biology",
    ordinal: 0,
    note_type: "basic",
    preview: "Own card",
    tags: [],
    flag: 0,
    state: "new",
    due_at: null,
    proficiency: 0,
    created_at: "2026-01-01T00:00:00Z",
  };

  it("ignores a library deck in the URL and lists the user's own cards", async () => {
    const calls = mockApi({
      ...me,
      "GET /decks": { body: [ownDeck, libraryDeck] },
      "GET /cards": { body: { total: 1, cards: [row] } },
    });
    renderApp("/cards?deck=l1");
    expect(await screen.findByRole("button", { name: "Own card" })).toBeInTheDocument();
    expect(screen.getByText(/That deck is in the library/)).toBeInTheDocument();
    const browses = calls.filter((c) => c.path.startsWith("/cards?")).map((c) => c.path);
    expect(browses).toHaveLength(1);
    expect(browses[0]).not.toContain("deck_id");
    expect(screen.getByLabelText("Deck")).toHaveValue("");
  });

  it("leaves the filter to the server when the decks cannot load", async () => {
    const calls = mockApi({
      ...me,
      "GET /decks": { status: 500 },
      "GET /cards": { body: { total: 1, cards: [row] } },
    });
    renderApp("/cards?deck=l1");
    expect(await screen.findByRole("button", { name: "Own card" })).toBeInTheDocument();
    expect(calls.find((c) => c.path.startsWith("/cards?"))?.path).toContain("deck_id=l1");
    expect(screen.queryByText(/That deck is in the library/)).toBeNull();
  });
});
