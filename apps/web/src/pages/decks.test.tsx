// SPDX-License-Identifier: AGPL-3.0-only
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { Deck, DeckStats } from "../api/types";
import { enqueue } from "../offline/reviewQueue";
import { finishOnboarding } from "../onboarding/state";
import { USER, mockApi, renderApp } from "../test/helpers";

const deck = (id: string, name: string, parent: string | null = null, owned = true): Deck => ({
  id,
  name,
  parent_id: parent,
  created_at: "2026-01-01T00:00:00Z",
  owned,
});

/** An admin's deck, as a member sees it (ADR 0024). */
const libraryDeck = (id: string, name: string, parent: string | null = null): Deck => deck(id, name, parent, false);

const STATS: DeckStats = {
  total: 10,
  new: 4,
  learning: 1,
  review: 5,
  mature: 2,
  suspended: 0,
  due: 3,
  proficiency: 62,
};

const me = { "GET /auth/me": { body: USER } };

describe("deck list", () => {
  it("teaches the next step when there are no decks, then adds one", async () => {
    let list: Deck[] = [];
    const calls = mockApi({
      ...me,
      "GET /decks": () => ({ body: list }),
      "POST /decks": (call) => {
        list = [deck("d1", (call.body as { name: string }).name)];
        return { status: 201, body: list[0] };
      },
    });
    // This user finished the guided setup before, so the empty list is shown.
    finishOnboarding(USER.id);
    renderApp("/");
    expect(await screen.findByText(/No decks yet/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "guided setup" })).toHaveAttribute("href", "/welcome");
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("New deck"), "  Biology  ");
    await user.click(screen.getByRole("button", { name: "Add deck" }));
    expect(await screen.findByRole("link", { name: "Biology" })).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ name: "Biology", parent_id: null });
  });

  it("shows each top-level deck as a tile with its initial and subdeck count", async () => {
    mockApi({
      ...me,
      "GET /decks": {
        body: [
          deck("a", "biology"),
          deck("b", "Cells", "a"),
          deck("c", "Organelles", "b"),
          deck("d", "Spanish"),
          deck("e", "Verbs", "d"),
          deck("f", "Chemistry"),
          deck("g", "   "),
        ],
      },
    });
    renderApp("/");
    const tile = (name: string) =>
      (screen.getAllByRole("link", { name }).at(0) as HTMLElement).closest("li.deck-tile") as HTMLElement;
    await screen.findByRole("link", { name: "biology" });
    expect(tile("biology")).toHaveTextContent(/^Bbiology2 subdecks/);
    expect(within(tile("Spanish")).getByText("1 subdeck")).toBeInTheDocument();
    expect(within(tile("Chemistry")).queryByText(/subdeck/)).toBeNull();
    // A tile's colour is stable for a deck and one of the defined tones.
    expect(tile("Chemistry").querySelector(".deck-avatar")?.className).toMatch(/^deck-avatar tone-[0-5]$/);
    // A name without a letter still gets a placeholder initial.
    const blank = screen.getAllByRole("listitem").find((item) => item.textContent.startsWith("#"));
    expect(blank).toBeDefined();
  });

  it("shows nested decks and can create one inside another", async () => {
    const calls = mockApi({
      ...me,
      "GET /decks": { body: [deck("a", "Biology"), deck("b", "Cells", "a")] },
      "POST /decks": { status: 422, body: { detail: "Invalid deck name." } },
    });
    renderApp("/");
    const outer = await screen.findByRole("link", { name: "Biology" });
    expect(within(outer.closest("li") as HTMLElement).getByRole("link", { name: "Cells" })).toBeInTheDocument();
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("New deck"), "x");
    await user.selectOptions(screen.getByLabelText("Inside"), "b");
    await user.click(screen.getByRole("button", { name: "Add deck" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid deck name.");
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ name: "x", parent_id: "b" });
  });

  it("lists library decks apart from the user's own, marked, and never as a parent", async () => {
    mockApi({
      ...me,
      "GET /decks": {
        body: [deck("a", "Biology"), libraryDeck("l1", "Anatomy"), libraryDeck("l2", "Bones", "l1")],
      },
    });
    renderApp("/");
    const own = await screen.findByRole("heading", { name: "Your decks" });
    expect(own.tagName).toBe("H2");
    const library = screen.getByRole("region", { name: "Library" });
    expect(
      within(library).getByText("Shared by your admin. Study them; your progress is your own."),
    ).toBeInTheDocument();
    const tile = within(library).getByRole("link", { name: "Anatomy" }).closest("li.deck-tile") as HTMLElement;
    expect(within(tile).getByText("Library")).toHaveClass("badge");
    expect(within(tile).getByRole("link", { name: "Bones" })).toHaveAttribute("href", "/decks/l2");
    expect(within(library).queryByRole("link", { name: "Biology" })).toBeNull();
    const ownTile = screen.getByRole("link", { name: "Biology" }).closest("li.deck-tile") as HTMLElement;
    expect(within(ownTile).queryByText("Library")).toBeNull();
    const parents = within(screen.getByLabelText("Inside")).getAllByRole("option");
    expect(parents.map((option) => option.textContent)).toEqual(["No parent", "Biology"]);
  });

  it("keeps the plain layout when there is no library", async () => {
    mockApi({ ...me, "GET /decks": { body: [deck("a", "Biology")] } });
    renderApp("/");
    await screen.findByRole("link", { name: "Biology" });
    expect(screen.queryByRole("heading", { name: "Your decks" })).toBeNull();
    expect(screen.queryByRole("region", { name: "Library" })).toBeNull();
  });

  it("lets a member with only library decks study them, still offering the guided setup", async () => {
    mockApi({ ...me, "GET /decks": { body: [libraryDeck("l1", "Anatomy")] } });
    renderApp("/");
    expect(await screen.findByText(/No decks of your own yet/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "guided setup" })).toHaveAttribute("href", "/welcome");
    expect(screen.getByRole("link", { name: "Anatomy" })).toHaveAttribute("href", "/decks/l1");
    // Nothing of their own to nest under, and no detour into the setup.
    expect(screen.queryByLabelText("Inside")).toBeNull();
    expect(screen.queryByRole("heading", { name: "Getting started" })).toBeNull();
  });

  it("shows a load failure", async () => {
    mockApi({ ...me, "GET /decks": { status: 500 } });
    renderApp("/");
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong on the server");
  });
});

describe("deck detail", () => {
  const decks = [deck("a", "Biology"), deck("b", "Cells", "a"), deck("c", "Membranes", "b")];

  it("shows statistics, the proficiency ring, sub-decks and the ways in", async () => {
    mockApi({ ...me, "GET /decks": { body: decks }, "GET /decks/b/stats": { body: STATS } });
    renderApp("/decks/b");
    expect(await screen.findByRole("heading", { name: "Cells" })).toBeInTheDocument();
    expect(await screen.findByRole("img", { name: "Proficiency 62%" })).toBeInTheDocument();
    expect(screen.getByText("Due now").nextSibling).toHaveTextContent("3");
    expect(
      within(screen.getByRole("navigation", { name: "Breadcrumb" })).getByRole("link", { name: "Biology" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Membranes" })).toHaveAttribute("href", "/decks/c");
    expect(screen.getByRole("link", { name: "Study now" })).toHaveAttribute("href", "/decks/b/study");
    expect(screen.getByRole("link", { name: "Browse cards" })).toHaveAttribute("href", "/cards?deck=b");
  });

  it("shows a library deck for study only, with its sub-decks", async () => {
    const calls = mockApi({
      ...me,
      "GET /decks": { body: [libraryDeck("l1", "Anatomy"), libraryDeck("l2", "Bones", "l1")] },
      "GET /decks/l1/stats": { body: STATS },
    });
    renderApp("/decks/l1");
    expect(await screen.findByRole("heading", { name: "Anatomy" })).toBeInTheDocument();
    expect(screen.getByText("From the library")).toHaveClass("badge");
    expect(screen.getByText(/only the admin can change it/)).toBeInTheDocument();
    expect(await screen.findByRole("img", { name: "Proficiency 62%" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Study now" })).toHaveAttribute("href", "/decks/l1/study");
    expect(screen.getByRole("link", { name: "Bones" })).toHaveAttribute("href", "/decks/l2");
    for (const name of ["Add card", "Generate from a file", "Browse cards", "Mock exam", "Share"]) {
      expect(screen.queryByRole("link", { name })).toBeNull();
    }
    for (const name of ["Rename", "Delete deck", "Add deck", "Anki package"]) {
      expect(screen.queryByRole("button", { name })).toBeNull();
    }
    expect(screen.queryByRole("heading", { name: "Manage" })).toBeNull();
    // The AI panels are the owner's and stay away.
    await waitFor(() => {
      expect(calls.some((c) => c.path === "/decks/l1/stats")).toBe(true);
    });
    expect(screen.queryByRole("heading", { name: "Deck summary" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "Ask your source documents" })).toBeNull();
  });

  it("says an empty library deck has no cards, without offering to add any", async () => {
    mockApi({
      ...me,
      "GET /decks": { body: [libraryDeck("l1", "Anatomy")] },
      "GET /decks/l1/stats": { body: { ...STATS, total: 0 } },
    });
    renderApp("/decks/l1");
    expect(await screen.findByText("This library deck has no cards yet.")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "import an Anki deck" })).toBeNull();
  });

  it("renames a deck", async () => {
    const calls = mockApi({
      ...me,
      "GET /decks": { body: decks },
      "GET /decks/a/stats": { body: STATS },
      "PATCH /decks/a": { body: deck("a", "Life") },
    });
    renderApp("/decks/a");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Rename" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await user.click(screen.getByRole("button", { name: "Rename" }));
    const input = screen.getByLabelText("Deck name");
    expect(input).toHaveFocus();
    await user.clear(input);
    await user.type(input, "Life");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("heading", { name: "Life" })).toBeInTheDocument();
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ name: "Life" });
  });

  it("asks before deleting a deck with sub-decks, then returns to the parent", async () => {
    let gone = false;
    const calls = mockApi({
      ...me,
      "GET /decks": () => ({ body: gone ? [deck("a", "Biology")] : decks }),
      "GET /decks/a/stats": { body: STATS },
      "GET /decks/b/stats": { body: STATS },
      "DELETE /decks/b": () => {
        gone = true;
        return { status: 204 };
      },
    });
    renderApp("/decks/b");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Delete deck" }));
    const dialog = screen.getByRole("alertdialog", { name: "Delete deck" });
    expect(dialog).toHaveTextContent("its 1 sub-deck and every card");
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toHaveFocus();
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Delete deck" }));
    await user.click(screen.getByRole("button", { name: "Delete for good" }));
    expect(await screen.findByRole("heading", { name: "Biology" })).toBeInTheDocument();
    expect(calls.some((c) => c.method === "DELETE" && c.path === "/decks/b")).toBe(true);
  });

  it("deletes a top-level deck and reports a failure", async () => {
    mockApi({
      ...me,
      "GET /decks": { body: [deck("a", "Biology"), deck("b", "Cells", "a"), deck("d", "Deep", "b")] },
      "GET /decks/a/stats": { status: 500 },
      "DELETE /decks/a": { status: 500 },
    });
    renderApp("/decks/a");
    const user = userEvent.setup();
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong");
    await user.click(screen.getByRole("button", { name: "Delete deck" }));
    expect(screen.getByRole("alertdialog")).toHaveTextContent("its 2 sub-decks");
    await user.click(screen.getByRole("button", { name: "Delete for good" }));
    await waitFor(() => {
      expect(screen.getAllByRole("alert").length).toBeGreaterThanOrEqual(2);
    });
  });

  it("deletes a lone top-level deck and goes home", async () => {
    let gone = false;
    mockApi({
      ...me,
      "GET /decks": () => ({ body: gone ? [] : [deck("a", "Biology")] }),
      "GET /decks/a/stats": { body: STATS },
      "DELETE /decks/a": () => {
        gone = true;
        return { status: 204 };
      },
    });
    // Home with no decks would otherwise open the guided setup for a new user.
    finishOnboarding(USER.id);
    renderApp("/decks/a");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Delete deck" }));
    expect(screen.getByRole("alertdialog")).toHaveTextContent("Delete “Biology” and every card in it?");
    await user.click(screen.getByRole("button", { name: "Delete for good" }));
    expect(await screen.findByText(/No decks yet/)).toBeInTheDocument();
  });

  it("handles an unknown deck and a failed load", async () => {
    mockApi({
      ...me,
      "GET /decks": { body: [] },
      "GET /decks/zz/stats": { status: 404, body: { detail: "Not found." } },
    });
    renderApp("/decks/zz");
    expect(await screen.findByRole("heading", { name: "Deck not found" })).toBeInTheDocument();
  });

  it("shows a deck list failure", async () => {
    mockApi({ ...me, "GET /decks": new TypeError("offline") });
    renderApp("/decks/a");
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not reach the server");
  });
});

describe("layout", () => {
  it("offers to resend the verification email", async () => {
    let fail = false;
    mockApi({
      "GET /auth/me": { body: { ...USER, email_verified: false } },
      "GET /decks": { body: [] },
      "POST /auth/verify-email/resend": () => (fail ? { status: 500 } : { status: 202 }),
    });
    renderApp("/");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Send the link again" }));
    expect(await screen.findByText("Check your inbox for the link.")).toBeInTheDocument();
    fail = true;
  });

  it("reports when the verification email cannot be sent", async () => {
    mockApi({
      "GET /auth/me": { body: { ...USER, email_verified: false } },
      "GET /decks": { body: [] },
      "POST /auth/verify-email/resend": { status: 500 },
    });
    renderApp("/");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Send the link again" }));
    expect(await screen.findByText(/Could not send it/)).toBeInTheDocument();
  });

  it("signs out, clearing what the device remembers", async () => {
    const remove = vi.fn(() => Promise.resolve(true));
    vi.stubGlobal("caches", { delete: remove });
    mockApi({ "GET /auth/me": { body: USER }, "GET /decks": { body: [] }, "POST /auth/logout": { status: 204 } });
    renderApp("/");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(localStorage.getItem("flashcards.lastUser")).toBeNull();
    expect(remove).toHaveBeenCalledWith("flashcards-study-data");
  });

  it("sends waiting answers before signing out", async () => {
    await enqueue({ idempotency_key: "k1", user_id: USER.id, card_id: "c1", deck_id: "d1", rating: 3, queued_at: 1 });
    let sent = 0;
    const calls = mockApi({
      "GET /auth/me": { body: USER },
      "GET /decks": { body: [] },
      "POST /cards/c1/review": () => {
        sent += 1;
        return sent === 1 ? { status: 503 } : { body: {} };
      },
      "POST /auth/logout": { status: 204 },
    });
    renderApp("/");
    await screen.findByText(/1 answer is waiting/);
    await userEvent.setup().click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    const paths = calls.map((c) => c.path);
    expect(paths.lastIndexOf("/cards/c1/review")).toBeLessThan(paths.indexOf("/auth/logout"));
  });

  it("will not pretend to sign out offline", async () => {
    mockApi({
      "GET /auth/me": { body: USER },
      "GET /decks": { body: [] },
      "POST /auth/logout": new TypeError("offline"),
    });
    renderApp("/");
    await userEvent.setup().click(await screen.findByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Connect to the internet to sign out.");
    expect(localStorage.getItem("flashcards.lastUser")).not.toBeNull();
  });

  it("treats an expired session as signed out and reports other sign-out failures", async () => {
    mockApi({ "GET /auth/me": { body: USER }, "GET /decks": { body: [] }, "POST /auth/logout": { status: 500 } });
    renderApp("/");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong on the server");

    mockApi({ "POST /auth/logout": { status: 401, body: { detail: "Not authenticated." } } });
    await user.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });

  it("shows offline state and waiting answers, and syncs when back online", async () => {
    await enqueue({ idempotency_key: "k1", user_id: USER.id, card_id: "c1", deck_id: "d1", rating: 3, queued_at: 1 });
    Object.defineProperty(navigator, "onLine", { configurable: true, get: () => false });
    const calls = mockApi({
      "GET /auth/me": { body: USER },
      "GET /decks": { body: [] },
      "POST /cards/c1/review": { status: 503 },
    });
    renderApp("/");
    await waitFor(() => {
      expect(screen.getByText(/You are offline/)).toHaveTextContent("1 answer is waiting to sync.");
    });

    Object.defineProperty(navigator, "onLine", { configurable: true, get: () => true });
    act(() => {
      window.dispatchEvent(new Event("online"));
    });
    expect(await screen.findByText(/Back online/)).toBeInTheDocument();
    expect(calls.some((c) => c.path === "/cards/c1/review")).toBe(true);

    mockApi({ "POST /cards/c1/review": { body: {} } });
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Sync now" }));
    await waitFor(() => {
      expect(screen.queryByText(/waiting to sync/)).not.toBeInTheDocument();
    });

    act(() => {
      window.dispatchEvent(new Event("offline"));
    });
    expect(await screen.findByText(/You are offline/)).not.toHaveTextContent("waiting");
  });

  it("shows a page-not-found message inside the app", async () => {
    mockApi(me);
    renderApp("/nowhere");
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
  });
});
