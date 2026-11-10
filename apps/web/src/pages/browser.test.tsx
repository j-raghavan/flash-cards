// SPDX-License-Identifier: AGPL-3.0-only
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { BrowserRow, CardDetail } from "../api/types";
import { USER, mockApi, renderApp, type Call } from "../test/helpers";
import { queryFromParams } from "./BrowserPage";

const row = (n: number, over: Partial<BrowserRow> = {}): BrowserRow => ({
  card_id: `c${n}`,
  note_id: `n${n}`,
  deck_id: "d1",
  deck_name: "Biology",
  ordinal: 0,
  note_type: "basic",
  preview: `Card ${n}`,
  tags: ["bio"],
  flag: 0,
  state: "new",
  due_at: null,
  proficiency: 40,
  created_at: "2026-01-01T00:00:00Z",
  ...over,
});

// Browsing is the admin's (members read cards from the deck page).
const base = {
  "GET /auth/me": { body: { ...USER, role: "admin" } },
  "GET /decks": {
    body: [
      { id: "d1", name: "Biology", parent_id: null, created_at: "", owned: true },
      { id: "d2", name: "Chemistry", parent_id: null, created_at: "", owned: true },
      // A library deck: its cards are not in the browser, so it is never offered.
      { id: "l1", name: "Anatomy", parent_id: null, created_at: "", owned: false },
    ],
  },
};

const browses = (calls: Call[]): string[] => calls.filter((c) => c.path.startsWith("/cards?")).map((c) => c.path);

describe("queryFromParams", () => {
  it("reads filters from the URL and ignores bad values", () => {
    expect(queryFromParams(new URLSearchParams("state=new&state=bogus&sort=nope&flag=9&page=-2&order=desc"))).toEqual({
      state: ["new"],
      sort: "created",
      order: "desc",
      limit: 50,
      offset: 0,
    });
    expect(queryFromParams(new URLSearchParams("deck=d1&tag=bio&q=cell&flag=0&sort=due&page=2&order=asc"))).toEqual({
      state: [],
      deck_id: "d1",
      tag: "bio",
      q: "cell",
      flag: 0,
      sort: "due",
      order: "asc",
      limit: 50,
      offset: 100,
    });
    expect(queryFromParams(new URLSearchParams("page=abc")).offset).toBe(0);
  });
});

describe("card browser", () => {
  it("filters through the URL", async () => {
    const calls = mockApi({ ...base, "GET /cards": { body: { total: 1, cards: [row(1, { flag: 2 })] } } });
    renderApp("/cards?deck=d1");
    expect(await screen.findByRole("button", { name: "Card 1" })).toHaveAttribute("aria-haspopup", "dialog");
    expect(screen.getByText("flag 2")).toBeInTheDocument();
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Search"), "cell");
    await user.type(screen.getByLabelText("Tag"), "bio");
    await user.click(screen.getByRole("button", { name: "Search" }));
    await user.click(screen.getByLabelText("learning"));
    await user.click(screen.getByLabelText("review"));
    await user.click(screen.getByLabelText("learning"));
    await user.selectOptions(screen.getByLabelText("Flag"), "3");
    await user.selectOptions(screen.getByLabelText("Sort by"), "proficiency");
    await user.selectOptions(screen.getByLabelText("Order"), "desc");
    await user.selectOptions(screen.getByLabelText("Deck"), "");
    await waitFor(() => {
      expect(browses(calls).at(-1)).toBe(
        "/cards?state=review&sort=proficiency&order=desc&limit=50&offset=0&tag=bio&q=cell&flag=3",
      );
    });
    expect(browses(calls)[0]).toBe(
      "/cards?state=&sort=created&order=asc&limit=50&offset=0&deck_id=d1".replace("state=&", ""),
    );
  });

  it("pages through results", async () => {
    const calls = mockApi({
      ...base,
      "GET /cards": (call) => ({
        body: { total: 120, cards: [row(call.path.includes("offset=50") ? 51 : 1)] },
      }),
    });
    renderApp("/cards");
    const user = userEvent.setup();
    expect(await screen.findByText("1 to 50 of 120")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("51 to 100 of 120")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Previous" }));
    expect(await screen.findByText("1 to 50 of 120")).toBeInTheDocument();
    expect(browses(calls).some((p) => p.includes("offset=50"))).toBe(true);
  });

  it("applies bulk actions to the selection", async () => {
    const calls = mockApi({
      ...base,
      "GET /cards": { body: { total: 2, cards: [row(1), row(2, { preview: "" })] } },
      "POST /cards/bulk": (call) => ({
        body: { cards: (call.body as { card_ids: string[] }).card_ids.length, notes: 0 },
      }),
    });
    renderApp("/cards");
    const user = userEvent.setup();
    await screen.findByRole("button", { name: "(empty)" });

    await user.click(screen.getByLabelText("Select Card 1"));
    expect(screen.getByText("1 selected")).toBeInTheDocument();
    const apply = screen.getByRole("button", { name: "Apply" });
    expect(apply).toBeDisabled(); // move needs a deck
    // Neither the deck filter nor a move offers a library deck.
    for (const picker of screen.getAllByLabelText("Deck")) {
      expect(within(picker).queryByRole("option", { name: "Anatomy" })).toBeNull();
      expect(within(picker).getByRole("option", { name: "Chemistry" })).toBeInTheDocument();
    }
    await user.selectOptions(screen.getByLabelText("Deck", { selector: ".bulk-bar select" }), "d2");
    await user.click(apply);
    expect(await screen.findByText("Updated 1 card.")).toBeInTheDocument();

    await user.click(screen.getByLabelText("Select all on this page"));
    expect(screen.getByText("2 selected")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Action"), "add_tags");
    await user.type(screen.getByLabelText("Tags"), "exam");
    await user.click(screen.getByRole("button", { name: "Apply" }));
    expect(await screen.findByText("Updated 2 cards.")).toBeInTheDocument();

    await user.click(screen.getByLabelText("Select all on this page"));
    await user.click(screen.getByLabelText("Select all on this page"));
    expect(screen.queryByText(/selected/)).not.toBeInTheDocument();

    await user.click(screen.getByLabelText("Select Card 1"));
    await user.click(screen.getByLabelText("Select (empty)"));
    await user.click(screen.getByLabelText("Select (empty)"));
    await user.selectOptions(screen.getByLabelText("Action"), "flag");
    await user.selectOptions(screen.getByLabelText("Flag", { selector: ".bulk-bar select" }), "0");
    await user.click(screen.getByRole("button", { name: "Apply" }));
    await screen.findByText("Updated 1 card.");

    await user.click(screen.getByLabelText("Select Card 1"));
    await user.selectOptions(screen.getByLabelText("Action"), "suspend");
    await user.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() => {
      expect(calls.filter((c) => c.path === "/cards/bulk")).toHaveLength(4);
    });

    const bodies = calls.filter((c) => c.path === "/cards/bulk").map((c) => c.body);
    expect(bodies).toEqual([
      { card_ids: ["c1"], action: "move", deck_id: "d2" },
      { card_ids: ["c1", "c2"], action: "add_tags", tags: ["exam"] },
      { card_ids: ["c1"], action: "flag", flag: 0 },
      { card_ids: ["c1"], action: "suspend" },
    ]);
  });

  it("asks before a bulk delete and shows a refusal", async () => {
    const calls = mockApi({
      ...base,
      "GET /cards": { body: { total: 2, cards: [row(1), row(2)] } },
      "POST /cards/bulk": { status: 422, body: { detail: [{ msg: "Value error, too many" }] } },
    });
    renderApp("/cards");
    const user = userEvent.setup();
    await user.click(await screen.findByLabelText("Select Card 1"));
    await user.selectOptions(screen.getByLabelText("Action"), "delete");
    await user.click(screen.getByRole("button", { name: "Apply" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Delete the notes behind 1 card?");
    await user.click(screen.getByLabelText("Select Card 2"));
    expect(screen.getByRole("alert")).toHaveTextContent("Delete the notes behind 2 cards?");
    expect(calls.some((c) => c.path === "/cards/bulk")).toBe(false);
    await user.selectOptions(screen.getByLabelText("Action"), "delete");
    await user.click(screen.getByRole("button", { name: "Apply" }));
    await user.click(screen.getByRole("button", { name: "Delete for good" }));
    expect(await screen.findByText("too many")).toBeInTheDocument();
  });

  it("warns an admin that a bulk delete erases members' progress on those cards", async () => {
    mockApi({
      ...base,
      "GET /cards": { body: { total: 1, cards: [row(1)] } },
    });
    renderApp("/cards");
    const user = userEvent.setup();
    await user.click(await screen.findByLabelText("Select Card 1"));
    await user.selectOptions(screen.getByLabelText("Action"), "delete");
    await user.click(screen.getByRole("button", { name: "Apply" }));
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Delete the notes behind 1 card? Every card of those notes goes too. They are in every member's library: members who studied them lose their progress on them.",
    );
  });

  it("explains an empty result and a failed search", async () => {
    mockApi({ ...base, "GET /cards": { body: { total: 0, cards: [] } } });
    renderApp("/cards");
    expect(await screen.findByText("No cards match these filters.")).toBeInTheDocument();
  });

  it("shows a failed search", async () => {
    mockApi({ ...base, "GET /cards": { status: 422, body: { detail: "bad filter" } } });
    renderApp("/cards");
    expect(await screen.findByRole("alert")).toHaveTextContent("bad filter");
  });
});

const detail = (n: number, over: Partial<CardDetail> = {}): CardDetail => ({
  id: `c${String(n)}`,
  deck_id: "d1",
  note_id: `n${String(n)}`,
  ordinal: 0,
  suspended: false,
  flag: 0,
  buried_until: null,
  note_type: "basic",
  fields: { front: `Front **${String(n)}**`, back: `Back ${String(n)}` },
  tags: [],
  owned: true,
  ...over,
});

describe("card view", () => {
  const three = {
    ...base,
    "GET /cards": {
      body: {
        total: 3,
        cards: [
          row(1, { tags: ["bio", "cells"], state: "review" }),
          row(2, { note_type: "cloze", tags: [] }),
          row(3, { deck_name: "Chemistry" }),
        ],
      },
    },
    "GET /cards/c1": { body: detail(1) },
    "GET /cards/c2": {
      body: detail(2, { note_type: "cloze", ordinal: 1, fields: { text: "{{c1::Paris}} is in {{c2::France}}" } }),
    },
    "GET /cards/c3": { body: detail(3) },
  };

  it("opens a card from its row, as it is studied, and steps through the page", async () => {
    const calls = mockApi(three);
    renderApp("/cards?tag=bio");
    const user = userEvent.setup();
    const opener = await screen.findByRole("button", { name: "Card 1" });
    await user.click(opener);
    const dialog = await screen.findByRole("dialog", { name: "Card 1 of 3" });
    expect(within(dialog).getByText("Biology")).toBeInTheDocument();
    expect(within(dialog).getByText("review")).toBeInTheDocument();
    expect(within(dialog).getByText("bio cells")).toBeInTheDocument();
    const content = await within(dialog).findByRole("article", { name: "Card content" });
    expect(within(content).getByText("1").tagName).toBe("STRONG"); // Markdown is rendered
    expect(content.querySelector("hr")).not.toBeNull();
    expect(within(content).getByText("Back 1")).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: "Edit" })).toHaveAttribute("href", "/notes/n1/edit");
    expect(within(dialog).getByRole("button", { name: "Previous card" })).toBeDisabled();

    // The answer can be hidden, and stays hidden from card to card until shown again.
    const hide = within(dialog).getByRole("button", { name: "Hide answer" });
    await user.click(hide);
    expect(hide).toHaveAttribute("aria-pressed", "true");
    expect(within(dialog).queryByText("Back 1")).toBeNull();
    await user.click(hide);
    expect(within(dialog).getByText("Back 1")).toBeInTheDocument();

    // Next, then the arrow keys; a cloze card shows its deletion, revealed.
    await user.click(within(dialog).getByRole("button", { name: "Next card" }));
    expect(await screen.findByRole("dialog", { name: "Card 2 of 3" })).toBeInTheDocument();
    expect(await within(dialog).findByText("[…]")).toBeInTheDocument();
    expect(within(dialog).getByText("Paris")).toHaveClass("cloze");
    expect(within(dialog).getByText("None")).toBeInTheDocument(); // no tags
    await user.keyboard("{ArrowRight}");
    expect(await screen.findByRole("dialog", { name: "Card 3 of 3" })).toBeInTheDocument();
    expect(within(dialog).getByText("Chemistry")).toBeInTheDocument();
    expect(await within(dialog).findByText("Back 3")).toBeInTheDocument();
    await user.keyboard("{ArrowRight}"); // the end of the page: nothing further
    expect(screen.getByRole("dialog", { name: "Card 3 of 3" })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Next card" })).toBeDisabled();
    await user.keyboard("{ArrowLeft}{ArrowLeft}");
    expect(await screen.findByRole("dialog", { name: "Card 1 of 3" })).toBeInTheDocument();
    await user.keyboard("{ArrowLeft}"); // and the start
    expect(screen.getByRole("dialog", { name: "Card 1 of 3" })).toBeInTheDocument();

    // Escape closes it and returns focus to the row that opened it, keeping the filters.
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(opener).toHaveFocus();
    expect(screen.getByLabelText("Tag")).toHaveValue("bio");
    expect(calls.filter((c) => c.path.startsWith("/cards?"))).toHaveLength(1);
  });

  it("keeps focus in the dialog when the arrow keys reach an end, and Escape always closes it", async () => {
    mockApi(three);
    renderApp("/cards");
    const user = userEvent.setup();
    const opener = await screen.findByRole("button", { name: "Card 2" });
    await user.click(opener);
    const dialog = await screen.findByRole("dialog", { name: "Card 2 of 3" });
    within(dialog).getByRole("button", { name: "Next card" }).focus();
    await user.keyboard("{ArrowRight}"); // Next is now disabled: focus moves to Previous
    expect(await screen.findByRole("dialog", { name: "Card 3 of 3" })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Previous card" })).toHaveFocus();
    await user.keyboard("{ArrowLeft}{ArrowLeft}"); // Previous is now disabled: focus moves to Next
    expect(await screen.findByRole("dialog", { name: "Card 1 of 3" })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Next card" })).toHaveFocus();
    // Even with focus outside the dialog, Escape closes it.
    (document.activeElement as HTMLElement).blur();
    expect(dialog).not.toContainElement(document.activeElement as HTMLElement);
    await user.keyboard("{Escape}");
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
    expect(opener).toHaveFocus();
  });

  it("moves focus to the other button on a page of two, where both ends are one step apart", async () => {
    mockApi({ ...three, "GET /cards": { body: { total: 2, cards: [row(1), row(2)] } } });
    renderApp("/cards");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Card 1" }));
    const dialog = await screen.findByRole("dialog", { name: "Card 1 of 2" });
    await user.click(within(dialog).getByRole("button", { name: "Next card" }));
    expect(await screen.findByRole("dialog", { name: "Card 2 of 2" })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Previous card" })).toHaveFocus();
    await user.click(within(dialog).getByRole("button", { name: "Previous card" }));
    expect(await screen.findByRole("dialog", { name: "Card 1 of 2" })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Next card" })).toHaveFocus();
  });

  it("leaves the arrow keys to a text field, and to a handler that already took them", async () => {
    mockApi(three);
    renderApp("/cards");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Card 2" }));
    await screen.findByRole("dialog", { name: "Card 2 of 3" });
    const field = document.createElement("input");
    document.body.append(field);
    field.focus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("dialog", { name: "Card 2 of 3" })).toBeInTheDocument();
    field.remove();
    const taken = new KeyboardEvent("keydown", { key: "ArrowLeft", bubbles: true, cancelable: true });
    taken.preventDefault();
    window.dispatchEvent(taken);
    expect(screen.getByRole("dialog", { name: "Card 2 of 3" })).toBeInTheDocument();
  });

  it("keeps focus in the dialog when a button reaches an end of the page", async () => {
    mockApi(three);
    renderApp("/cards");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Card 2" }));
    const dialog = await screen.findByRole("dialog", { name: "Card 2 of 3" });
    await user.click(within(dialog).getByRole("button", { name: "Next card" }));
    expect(within(dialog).getByRole("button", { name: "Previous card" })).toHaveFocus();
    await user.click(within(dialog).getByRole("button", { name: "Previous card" }));
    await user.click(within(dialog).getByRole("button", { name: "Previous card" }));
    expect(await screen.findByRole("dialog", { name: "Card 1 of 3" })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Next card" })).toHaveFocus();
    // Arrows with a modifier are left to the browser.
    await user.keyboard("{Alt>}{ArrowRight}{/Alt}");
    expect(screen.getByRole("dialog", { name: "Card 1 of 3" })).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("offers Edit for a card whose payload does not say who owns it", async () => {
    const unmarked = detail(1);
    delete unmarked.owned;
    mockApi({ ...three, "GET /cards/c1": { body: unmarked } });
    renderApp("/cards");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Card 1" }));
    const dialog = await screen.findByRole("dialog", { name: "Card 1 of 3" });
    expect(await within(dialog).findByRole("link", { name: "Edit" })).toHaveAttribute("href", "/notes/n1/edit");
  });

  it("offers no Edit for a card that is not the caller's, and shows a card loading or failing", async () => {
    let release = (): void => undefined;
    const loaded = new Promise<void>((resolve) => {
      release = resolve;
    });
    mockApi({
      ...three,
      "GET /cards/c1": { body: detail(1, { owned: false }) },
      "GET /cards/c2": { status: 404, body: { detail: "Card not found." } },
      "GET /cards/c3": async () => {
        await loaded;
        return { body: detail(3) };
      },
    });
    renderApp("/cards");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Card 1" }));
    const dialog = await screen.findByRole("dialog", { name: "Card 1 of 3" });
    expect(await within(dialog).findByText("Back 1")).toBeInTheDocument();
    expect(within(dialog).queryByRole("link", { name: "Edit" })).toBeNull();
    await user.keyboard("{ArrowRight}");
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Card not found.");
    expect(within(dialog).queryByRole("article")).toBeNull();
    await user.keyboard("{ArrowRight}");
    expect(await within(dialog).findByText("Loading card…")).toBeInTheDocument();
    release();
    expect(await within(dialog).findByText("Back 3")).toBeInTheDocument();
  });
});
