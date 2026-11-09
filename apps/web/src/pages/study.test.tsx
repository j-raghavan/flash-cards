// SPDX-License-Identifier: AGPL-3.0-only
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { Card, CardDetail } from "../api/types";
import { enqueue, pending } from "../offline/reviewQueue";
import { USER, mockApi, renderApp, type Call } from "../test/helpers";

const card = (id: string, ordinal = 0): Card => ({
  id,
  deck_id: "d1",
  note_id: `n-${id}`,
  ordinal,
  suspended: false,
  flag: 0,
  buried_until: null,
});

const basic = (id: string, front: string, back: string): CardDetail => ({
  ...card(id),
  note_type: "basic",
  fields: { front, back },
  tags: [],
});

const me = { "GET /auth/me": { body: USER } };

function reviews(calls: Call[]): Call[] {
  return calls.filter((c) => c.method === "POST" && c.path.endsWith("/review"));
}

describe("study session", () => {
  it("reveals with Space, rates with number keys and sends one idempotent answer per card", async () => {
    let round = 0;
    const calls = mockApi({
      ...me,
      "GET /decks/d1/review": () => {
        round += 1;
        return { body: round === 1 ? [card("c1"), card("c2")] : [] };
      },
      "GET /cards/c1": { body: basic("c1", "What is **ATP**?", "Energy currency") },
      "GET /cards/c2": { body: basic("c2", "$x^2$", "square") },
      "POST /cards/c1/review": { body: {} },
      "POST /cards/c2/review": { body: {} },
    });
    renderApp("/decks/d1/study");
    const user = userEvent.setup();

    expect(await screen.findByText("ATP")).toBeInTheDocument();
    expect(screen.getByText("Card 1 of 2")).toBeInTheDocument();
    expect(screen.queryByText("Energy currency")).not.toBeInTheDocument();
    await user.keyboard("3"); // rating before reveal does nothing
    expect(reviews(calls)).toHaveLength(0);
    await user.keyboard(" ");
    expect(screen.getByText("Energy currency")).toBeInTheDocument();
    await user.keyboard("3");

    expect(await screen.findByText("Card 2 of 2")).toBeInTheDocument();
    await screen.findByText("square", { exact: false }).catch(() => undefined);
    await user.click(await screen.findByRole("button", { name: /Show answer/ }));
    await user.click(screen.getByRole("button", { name: /Easy/ }));

    expect(await screen.findByRole("heading", { name: "All caught up" })).toBeInTheDocument();
    expect(screen.getByText("You reviewed 2 cards.")).toBeInTheDocument();
    const sent = reviews(calls);
    expect(sent.map((c) => c.body)).toEqual([
      { rating: 3, idempotency_key: expect.any(String) as string },
      { rating: 4, idempotency_key: expect.any(String) as string },
    ]);
    const keys = sent.map((c) => (c.body as { idempotency_key: string }).idempotency_key);
    expect(new Set(keys).size).toBe(2);
  });

  it("ignores keys typed into a field and keys with modifiers", async () => {
    mockApi({
      ...me,
      "GET /decks/d1/review": { body: [card("c1")] },
      "GET /cards/c1": { body: basic("c1", "Q", "A") },
    });
    renderApp("/decks/d1/study");
    await screen.findByText("Q");
    const user = userEvent.setup();
    await user.keyboard("{Control>} {/Control}");
    expect(screen.queryByText("A")).not.toBeInTheDocument();
    const input = document.createElement("input");
    document.body.appendChild(input);
    input.focus();
    await user.keyboard(" ");
    expect(screen.queryByText("A")).not.toBeInTheDocument();
    input.remove();
    await user.keyboard("{Enter}");
    expect(screen.getByText("A")).toBeInTheDocument();
    await user.keyboard("x");
    expect(screen.getByText("A")).toBeInTheDocument();
  });

  it("shows cloze cards with the current deletion hidden", async () => {
    mockApi({
      ...me,
      "GET /decks/d1/review": { body: [card("c1", 2)] },
      "GET /cards/c1": {
        body: {
          ...card("c1", 2),
          note_type: "cloze",
          fields: { text: "{{c1::Paris}} in {{c2::France::country}}" },
          tags: [],
        },
      },
    });
    renderApp("/decks/d1/study");
    expect(await screen.findByText("[country]")).toBeInTheDocument();
    expect(screen.getByText("Paris", { exact: false })).toBeInTheDocument();
    await userEvent.setup().keyboard(" ");
    expect(screen.getByText("France")).toBeInTheDocument();
  });

  it("queues answers on the device when offline and leaves them out of the next queue", async () => {
    await enqueue({ idempotency_key: "old", user_id: USER.id, card_id: "c0", deck_id: "d1", rating: 1, queued_at: 1 });
    const calls = mockApi({
      ...me,
      "GET /decks/d1/review": { body: [card("c0"), card("c1")] },
      "GET /cards/c1": { body: basic("c1", "Q1", "A1") },
      "POST /cards/c0/review": new TypeError("offline"),
      "POST /cards/c1/review": new TypeError("offline"),
    });
    renderApp("/decks/d1/study");
    expect(await screen.findByText("Q1")).toBeInTheDocument();
    expect(screen.getByText("Card 1 of 1")).toBeInTheDocument();
    const user = userEvent.setup();
    await user.keyboard(" 1");
    expect(await screen.findByText(/1 will sync when you are back online/)).toBeInTheDocument();
    expect((await pending(USER.id)).map((r) => r.card_id).sort()).toEqual(["c0", "c1"]);
    expect(calls.filter((c) => c.path.startsWith("/decks/d1/review")).length).toBeGreaterThanOrEqual(2);
  });

  it("brings back cards that are due again after the round", async () => {
    let round = 0;
    mockApi({
      ...me,
      "GET /decks/d1/review": () => {
        round += 1;
        return { body: round <= 2 ? [card("c1")] : [] };
      },
      "GET /cards/c1": { body: basic("c1", "Again?", "Yes") },
      "POST /cards/c1/review": { body: {} },
    });
    renderApp("/decks/d1/study");
    const user = userEvent.setup();
    await screen.findByText("Again?");
    await user.keyboard(" 1");
    await waitFor(() => {
      expect(round).toBe(2);
    });
    expect(await screen.findByText("Again?")).toBeInTheDocument();
    await user.keyboard(" 3");
    expect(await screen.findByText("You reviewed 2 cards.")).toBeInTheDocument();
  });

  it("ends the session quietly when a later round cannot load", async () => {
    let round = 0;
    mockApi({
      ...me,
      "GET /decks/d1/review": () => {
        round += 1;
        return round === 1 ? { body: [card("c1")] } : new TypeError("offline");
      },
      "GET /cards/c1": { body: basic("c1", "Q", "A") },
      "POST /cards/c1/review": { body: {} },
    });
    renderApp("/decks/d1/study");
    await screen.findByText("Q");
    await userEvent.setup().keyboard(" 2");
    expect(await screen.findByText("You reviewed 1 card.")).toBeInTheDocument();
  });

  it("reports an answer the server refuses", async () => {
    mockApi({
      ...me,
      "GET /decks/d1/review": { body: [card("c1"), card("c2")] },
      "GET /cards/c1": { body: basic("c1", "Q", "A") },
      "GET /cards/c2": { body: basic("c2", "Q2", "A2") },
      "POST /cards/c1/review": { status: 404, body: { detail: "Not found." } },
    });
    renderApp("/decks/d1/study");
    await screen.findByText("Q");
    await userEvent.setup().keyboard(" 4");
    expect(await screen.findByRole("alert")).toHaveTextContent("Not found.");
  });

  it("lets a card that will not load be skipped", async () => {
    mockApi({
      ...me,
      "GET /decks/d1/review": { body: [card("c1"), card("c2")] },
      "GET /cards/c1": { status: 500 },
      "GET /cards/c2": { body: basic("c2", "Second", "A") },
    });
    renderApp("/decks/d1/study");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Skip this card" }));
    expect(await screen.findByText("Second")).toBeInTheDocument();
  });

  it("says when nothing is due and when the queue cannot load", async () => {
    mockApi({ ...me, "GET /decks/d1/review": { body: [] } });
    renderApp("/decks/d1/study");
    expect(await screen.findByText("Nothing is due in this deck right now.")).toBeInTheDocument();
  });

  it("ignores a queue answer that arrives after leaving", async () => {
    let fail: (reason: Error) => void = () => undefined;
    mockApi({ ...me, "GET /decks/d1/review": () => new Promise((_resolve, reject) => (fail = reject)) });
    const { unmount } = renderApp("/decks/d1/study");
    await screen.findByText("Loading your cards…");
    unmount();
    fail(new TypeError("offline"));
    await Promise.resolve();
  });

  it("shows a queue that cannot load", async () => {
    mockApi({ ...me, "GET /decks/d1/review": new TypeError("offline") });
    renderApp("/decks/d1/study");
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not reach the server");
  });
});
