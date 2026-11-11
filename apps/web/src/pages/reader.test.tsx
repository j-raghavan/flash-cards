// SPDX-License-Identifier: AGPL-3.0-only
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { Card, CardDetail, Deck, DeckStats } from "../api/types";
import { USER, mockApi, renderApp } from "../test/helpers";
import { readerIndex } from "./DeckReaderPage";

const deck = (id: string, name: string, parent: string | null = null, owned = true): Deck => ({
  id,
  name,
  parent_id: parent,
  created_at: "2026-01-01T00:00:00Z",
  owned,
});

const card = (n: number): Card => ({
  id: `c${String(n)}`,
  deck_id: "l2",
  note_id: `n${String(n)}`,
  ordinal: 0,
  suspended: false,
  flag: 0,
  buried_until: null,
});

const detail = (n: number, over: Partial<CardDetail> = {}): CardDetail => ({
  ...card(n),
  note_type: "basic",
  fields: { front: `Front **${String(n)}**`, back: `Back ${String(n)}` },
  tags: [],
  owned: false,
  ...over,
});

const STATS: DeckStats = {
  total: 3,
  new: 3,
  learning: 0,
  review: 0,
  mature: 0,
  suspended: 0,
  due: 3,
  proficiency: 0,
};

/** A member reading a library sub-deck of three cards. */
const library = {
  "GET /auth/me": { body: USER },
  "GET /decks": { body: [deck("l1", "AP Math", null, false), deck("l2", "Algebra", "l1", false)] },
  "GET /decks/l2/cards": { body: [card(1), card(2), card(3)] },
  "GET /cards/c1": { body: detail(1) },
  "GET /cards/c2": { body: detail(2) },
  "GET /cards/c3": { body: detail(3) },
};

describe("deck reader", () => {
  it("is offered on every deck page with cards, library decks included", async () => {
    mockApi({ ...library, "GET /decks/l2/stats": { body: STATS } });
    renderApp("/decks/l2");
    expect(await screen.findByRole("link", { name: "Read cards" })).toHaveAttribute("href", "/decks/l2/read");
  });

  it("is not offered on a deck with no cards", async () => {
    mockApi({ ...library, "GET /decks/l2/stats": { body: { ...STATS, total: 0 } } });
    renderApp("/decks/l2");
    expect(await screen.findByText("This library deck has no cards yet.")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Read cards" })).toBeNull();
  });

  it("steps through a library deck's cards, read-only, keeping the position in the address", async () => {
    const calls = mockApi(library);
    renderApp("/decks/l2/read");
    expect(await screen.findByRole("heading", { name: "Read: Algebra" })).toBeInTheDocument();
    const crumbs = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(within(crumbs).getByRole("link", { name: "AP Math" })).toHaveAttribute("href", "/decks/l1");
    expect(within(crumbs).getByRole("link", { name: "Algebra" })).toHaveAttribute("href", "/decks/l2");
    expect(screen.getByText(/^Card \d of 3$/)).toHaveTextContent("Card 1 of 3");
    const content = await screen.findByRole("article", { name: "Card content" });
    expect(within(content).getByText("1").tagName).toBe("STRONG"); // Markdown is rendered
    expect(within(content).getByText("Back 1")).toBeInTheDocument();
    // A library card is never editable from here.
    expect(screen.queryByRole("link", { name: "Edit this card" })).toBeNull();
    expect(screen.getByRole("link", { name: "Study this deck" })).toHaveAttribute("href", "/decks/l2/study");
    const previous = screen.getByRole("button", { name: "Previous card" });
    const next = screen.getByRole("button", { name: "Next card" });
    expect(previous).toBeDisabled();

    const user = userEvent.setup();
    // Answers can be hidden, and stay hidden from card to card until shown again;
    // the button names what it will do next.
    const toggle = screen.getByRole("button", { name: "Hide answers" });
    await user.click(toggle);
    expect(toggle).toHaveAccessibleName("Show answers");
    expect(screen.queryByText("Back 1")).toBeNull();

    await user.click(next);
    expect(screen.getByText(/^Card \d of 3$/)).toHaveTextContent("Card 2 of 3");
    expect(await screen.findByText("2")).toHaveProperty("tagName", "STRONG");
    expect(screen.queryByText("Back 2")).toBeNull();
    await user.click(toggle);
    expect(await screen.findByText("Back 2")).toBeInTheDocument();
    expect(toggle).toHaveAccessibleName("Hide answers");

    // The arrow keys step too, and focus stays on a usable button at either end.
    await user.keyboard("{ArrowRight}");
    expect(screen.getByText(/^Card \d of 3$/)).toHaveTextContent("Card 3 of 3");
    expect(next).toBeDisabled();
    expect(previous).toHaveFocus();
    await user.keyboard("{ArrowRight}"); // the end: nothing further
    expect(screen.getByText(/^Card \d of 3$/)).toHaveTextContent("Card 3 of 3");
    await user.keyboard("{Shift>}{ArrowLeft}{/Shift}"); // a modified arrow is not ours
    expect(screen.getByText(/^Card \d of 3$/)).toHaveTextContent("Card 3 of 3");
    await user.keyboard("{ArrowLeft}{ArrowLeft}");
    expect(screen.getByText(/^Card \d of 3$/)).toHaveTextContent("Card 1 of 3");
    expect(next).toHaveFocus();
    await user.keyboard("{ArrowLeft}"); // and the start
    expect(screen.getByText(/^Card \d of 3$/)).toHaveTextContent("Card 1 of 3");

    // Reading never reviews, edits or reschedules anything.
    expect(calls.every((c) => c.method === "GET")).toBe(true);
  });

  it("opens the card the address names, and offers Edit on the caller's own card", async () => {
    mockApi({
      ...library,
      "GET /decks": { body: [deck("l2", "Algebra")] },
      "GET /cards/c2": { body: detail(2, { owned: true }) },
    });
    renderApp("/decks/l2/read?card=2");
    expect(await screen.findByRole("link", { name: "Edit this card" })).toHaveAttribute("href", "/notes/n2/edit");
    expect(screen.getByText(/^Card \d of 3$/)).toHaveTextContent("Card 2 of 3");
    const crumbs = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(
      within(crumbs)
        .getAllByRole("link")
        .map((link) => link.textContent),
    ).toEqual(["Decks", "Algebra"]);
  });

  it("moves focus to the other button in a deck of two, where both ends are one step apart", async () => {
    mockApi({ ...library, "GET /decks/l2/cards": { body: [card(1), card(2)] } });
    renderApp("/decks/l2/read");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Next card" }));
    expect(screen.getByText(/^Card \d of 2$/)).toHaveTextContent("Card 2 of 2");
    expect(screen.getByRole("button", { name: "Previous card" })).toHaveFocus();
    await user.click(screen.getByRole("button", { name: "Previous card" }));
    expect(screen.getByText(/^Card \d of 2$/)).toHaveTextContent("Card 1 of 2");
    expect(screen.getByRole("button", { name: "Next card" })).toHaveFocus();
  });

  it("leaves the arrow keys to a text field, and to a handler that already took them", async () => {
    mockApi(library);
    renderApp("/decks/l2/read?card=2");
    await screen.findByRole("button", { name: "Next card" });
    const user = userEvent.setup();
    const field = document.createElement("input");
    document.body.append(field);
    field.focus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByText(/^Card \d of 3$/)).toHaveTextContent("Card 2 of 3");
    field.remove();
    const taken = new KeyboardEvent("keydown", { key: "ArrowLeft", bubbles: true, cancelable: true });
    taken.preventDefault();
    window.dispatchEvent(taken);
    expect(screen.getByText(/^Card \d of 3$/)).toHaveTextContent("Card 2 of 3");
  });

  it("says when a deck has no cards to read", async () => {
    mockApi({ ...library, "GET /decks/l2/cards": { body: [] } });
    renderApp("/decks/l2/read");
    expect(await screen.findByText("This deck has no cards to read yet.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to the deck" })).toHaveAttribute("href", "/decks/l2");
    expect(screen.queryByRole("button", { name: "Next card" })).toBeNull();
  });

  it("says when the deck is not there", async () => {
    mockApi({ ...library, "GET /decks": { body: [] } });
    renderApp("/decks/l2/read");
    expect(await screen.findByRole("heading", { name: "Deck not found" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to your decks" })).toHaveAttribute("href", "/");
  });

  it("shows why the deck or a card could not load", async () => {
    mockApi({ ...library, "GET /decks/l2/cards": { status: 404, body: { detail: "Deck not found." } } });
    const { unmount } = renderApp("/decks/l2/read");
    expect(await screen.findByRole("alert")).toHaveTextContent("Deck not found.");
    unmount();

    mockApi({ ...library, "GET /cards/c1": { status: 404, body: { detail: "Card not found." } } });
    renderApp("/decks/l2/read");
    expect(await screen.findByRole("alert")).toHaveTextContent("Card not found.");
    // The others can still be read.
    await userEvent.setup().click(screen.getByRole("button", { name: "Next card" }));
    await waitFor(() => {
      expect(screen.getByText("Back 2")).toBeInTheDocument();
    });
  });
});

describe("readerIndex", () => {
  it("reads a 1-based position, falling back to the first card", () => {
    expect(readerIndex("2", 3)).toBe(1);
    expect(readerIndex("3", 3)).toBe(2);
    for (const asked of [null, "", "0", "4", "-1", "1.5", "two"]) {
      expect(readerIndex(asked, 3)).toBe(0);
    }
  });
});
