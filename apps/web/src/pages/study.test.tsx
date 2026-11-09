// SPDX-License-Identifier: AGPL-3.0-only
import { screen, waitFor, within } from "@testing-library/react";
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

  it("keeps focus with the session and leaves links and buttons their own keys", async () => {
    mockApi({
      ...me,
      "GET /decks/d1/review": { body: [card("c1")] },
      "GET /cards/c1": { body: basic("c1", "Q", "A") },
      "GET /decks": { body: [] },
      "GET /decks/d1/stats": { status: 404, body: { detail: "Not found." } },
    });
    renderApp("/decks/d1/study");
    const article = await screen.findByRole("article", { name: "Card 1 of 1" });
    await waitFor(() => {
      expect(article).toHaveFocus();
    });
    const user = userEvent.setup();
    await user.keyboard(" ");
    expect(screen.getByRole("group", { name: "How well did you know it?" })).toHaveFocus();
    screen.getByRole("link", { name: "Back to deck" }).focus();
    await user.keyboard("{Enter}");
    expect(await screen.findByRole("heading", { name: "Deck not found" })).toBeInTheDocument();
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

  it("shows an image occlusion card with its own masks hidden, then revealed", async () => {
    mockApi({
      ...me,
      "GET /decks/d1/review": { body: [card("c1", 2)] },
      "GET /cards/c1": {
        body: {
          ...card("c1", 2),
          note_type: "image_occlusion",
          fields: { header: "Label the heart", back_extra: "" },
          tags: [],
          occlusion: {
            image_id: "img 1",
            masks: [
              { id: 1, left: 0.1, top: 0.1, width: 0.2, height: 0.2 },
              { id: 2, left: 0.5, top: 0.5, width: 0.25, height: 0.1 },
            ],
          },
        },
      },
    });
    const { container } = renderApp("/decks/d1/study");
    expect(await screen.findByText("Label the heart")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Image with hidden parts" })).toHaveAttribute("src", "/api/media/img%201");
    expect(container.querySelectorAll(".mask")).toHaveLength(2);
    const own = container.querySelector(".mask-own") as HTMLElement;
    expect(own.style.left).toBe("50%");
    await userEvent.setup().keyboard(" ");
    expect(container.querySelectorAll(".mask")).toHaveLength(1);
    expect(container.querySelector(".mask-own")).toBeNull();
  });

  it("shows a custom card through its template", async () => {
    mockApi({
      ...me,
      "GET /decks/d1/review": { body: [card("c1")] },
      "GET /cards/c1": {
        body: {
          ...card("c1"),
          note_type: "custom",
          fields: { Word: "chat", Meaning: "cat" },
          tags: [],
          template: { ordinal: 0, name: "Recognize", front: "**{{Word}}**", back: "{{FrontSide}} means {{Meaning}}" },
        },
      },
    });
    renderApp("/decks/d1/study");
    expect(await screen.findByText("chat")).toBeInTheDocument();
    await userEvent.setup().keyboard(" ");
    expect(screen.getByText(/means cat/)).toBeInTheDocument();
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

  it("titles the page", async () => {
    mockApi({ ...me, "GET /decks/d1/review": { body: [] } });
    renderApp("/decks/d1/study");
    await screen.findByText("Nothing is due in this deck right now.");
    await waitFor(() => {
      expect(document.title).toBe("Study · Flash-Cards");
    });
  });
});

const goal = (reviews: number, justCompleted = false) => ({
  date: "2026-11-08",
  time_zone: "UTC",
  reviews,
  daily_goal: 2,
  goal_met: reviews >= 2,
  just_completed: justCompleted,
});

describe("undo, daily goal and editing in a session", () => {
  it("counts toward the daily goal, celebrates it, and undoes the last answer", async () => {
    const calls = mockApi({
      ...me,
      "GET /decks/d1/review": { body: [card("c1"), card("c2")] },
      "GET /cards/c1": { body: basic("c1", "Q1", "A1") },
      "GET /cards/c2": { body: basic("c2", "Q2", "A2") },
      "POST /cards/c1/review": { body: { review_id: "r1", daily_goal: goal(1) } },
      "POST /cards/c2/review": { body: { review_id: "r2", daily_goal: goal(2, true) } },
      "POST /reviews/r1/undo": { body: { review_id: "r1", card_id: "c1", schedule: null, daily_goal: goal(0) } },
    });
    renderApp("/decks/d1/study");
    const user = userEvent.setup();
    await screen.findByText("Q1");
    expect(screen.queryByRole("button", { name: /Undo last answer/ })).not.toBeInTheDocument();
    await user.keyboard(" 3");
    expect(await screen.findByText("Today: 1 of 2")).toBeInTheDocument();
    await screen.findByText("Q2");
    await user.keyboard("u");
    expect(await screen.findByText("Today: 0 of 2")).toBeInTheDocument();
    expect(await screen.findByText("Card 1 of 2")).toBeInTheDocument();
    expect(await screen.findByText("Q1")).toBeInTheDocument();
    expect(screen.queryByText("A1")).not.toBeInTheDocument();
    expect(calls.filter((c) => c.path === "/reviews/r1/undo")).toHaveLength(1);
    expect(screen.queryByRole("button", { name: /Undo last answer/ })).not.toBeInTheDocument();

    await user.keyboard(" 3");
    await screen.findByText("Q2");
    await user.keyboard(" 4");
    expect(await screen.findByText("Daily goal reached: 2 cards today. Well done.")).toBeInTheDocument();
  });

  it("undoes the answer that ended a round from the summary, with Ctrl+Z", async () => {
    let round = 0;
    mockApi({
      ...me,
      "GET /decks/d1/review": () => {
        round += 1;
        return { body: round === 1 ? [card("c1")] : [] };
      },
      "GET /cards/c1": { body: basic("c1", "Only", "A") },
      "POST /cards/c1/review": { body: { review_id: "r1", daily_goal: goal(1) } },
      "POST /reviews/r1/undo": { body: { review_id: "r1", card_id: "c1", schedule: null, daily_goal: goal(0) } },
    });
    renderApp("/decks/d1/study");
    const user = userEvent.setup();
    await screen.findByText("Only");
    await user.keyboard(" 2");
    expect(await screen.findByText("You reviewed 1 card.")).toBeInTheDocument();
    await screen.findByRole("button", { name: /Undo last answer/ });
    await user.keyboard("{Control>}z{/Control}");
    expect(await screen.findByText("Only")).toBeInTheDocument();
    expect(screen.getByText("Card 1 of 1")).toBeInTheDocument();
    await user.keyboard("{Control>}z{/Control}"); // nothing left to undo
    expect(screen.getByText("Only")).toBeInTheDocument();
  });

  it("reports an undo the server refuses and offers no second try", async () => {
    mockApi({
      ...me,
      "GET /decks/d1/review": { body: [card("c1"), card("c2")] },
      "GET /cards/c1": { body: basic("c1", "Q1", "A1") },
      "GET /cards/c2": { body: basic("c2", "Q2", "A2") },
      "POST /cards/c1/review": { body: { review_id: "r1" } },
      "POST /reviews/r1/undo": { status: 409, body: { detail: "That answer can no longer be undone." } },
    });
    renderApp("/decks/d1/study");
    const user = userEvent.setup();
    await screen.findByText("Q1");
    await user.keyboard(" 3");
    await user.click(await screen.findByRole("button", { name: /Undo last answer/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("That answer can no longer be undone.");
    expect(screen.getByText("Q2")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Undo last answer/ })).not.toBeInTheDocument();
  });

  it("offers no undo for an answer queued offline or from a server without undo", async () => {
    mockApi({
      ...me,
      "GET /decks/d1/review": { body: [card("c1"), card("c2"), card("c3")] },
      "GET /cards/c1": { body: basic("c1", "Q1", "A1") },
      "GET /cards/c2": { body: basic("c2", "Q2", "A2") },
      "GET /cards/c3": { body: basic("c3", "Q3", "A3") },
      "POST /cards/c1/review": { body: {} },
      "POST /cards/c2/review": new TypeError("offline"),
    });
    renderApp("/decks/d1/study");
    const user = userEvent.setup();
    await screen.findByText("Q1");
    await user.keyboard(" 3");
    await screen.findByText("Q2");
    await user.keyboard(" 3");
    await screen.findByText("Q3");
    await user.keyboard("u{Meta>}z{/Meta}");
    expect(screen.queryByRole("button", { name: /Undo last answer/ })).not.toBeInTheDocument();
    expect(screen.getByText("Q3")).toBeInTheDocument();
  });

  it("edits the card in a dialog and carries on with the edited card", async () => {
    let front = "Old question";
    const calls = mockApi({
      ...me,
      "GET /decks/d1/review": { body: [card("c1")] },
      "GET /cards/c1": () => ({ body: basic("c1", front, "Answer") }),
      "GET /notes/n-c1": {
        body: {
          id: "n-c1",
          deck_id: "d1",
          note_type: "basic",
          fields: { front: "Old question", back: "Answer" },
          tags: [],
          created_at: "2026-01-01T00:00:00Z",
          cards: [card("c1")],
        },
      },
      "PATCH /notes/n-c1": () => {
        front = "New question";
        return { body: {} };
      },
    });
    renderApp("/decks/d1/study");
    const user = userEvent.setup();
    await screen.findByText("Old question");
    await user.keyboard("e");
    const dialog = await screen.findByRole("dialog", { name: "Edit card" });
    await waitFor(() => {
      expect(dialog).toHaveFocus();
    });
    const field = await within(dialog).findByLabelText("Front");
    await user.keyboard(" "); // keys in the dialog do not drive the session
    expect(screen.queryByRole("button", { name: /Again/ })).not.toBeInTheDocument();
    await user.clear(field);
    await user.type(field, "New question");
    await user.click(within(dialog).getByRole("button", { name: "Save changes" }));
    expect(await screen.findByText("New question")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByRole("article", { name: "Card 1 of 1" })).toHaveFocus(); // where focus was before
    });
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({
      fields: { front: "New question", back: "Answer" },
      tags: [],
    });
  });

  it("closes the editor with Escape and shows a card the edit removed as one to skip", async () => {
    let gone = false;
    mockApi({
      ...me,
      "GET /decks/d1/review": { body: [card("c1", 2)] },
      "GET /cards/c1": () =>
        gone
          ? { status: 404, body: { detail: "Card not found." } }
          : { body: { ...card("c1", 2), note_type: "cloze", fields: { text: "{{c1::a}} {{c2::b}}" }, tags: [] } },
      "GET /notes/n-c1": {
        body: {
          id: "n-c1",
          deck_id: "d1",
          note_type: "cloze",
          fields: { text: "{{c1::a}} {{c2::b}}" },
          tags: [],
          created_at: "2026-01-01T00:00:00Z",
          cards: [card("c1", 2)],
        },
      },
      "PATCH /notes/n-c1": () => {
        gone = true;
        return { body: {} };
      },
    });
    renderApp("/decks/d1/study");
    const user = userEvent.setup();
    await screen.findByText("[…]");
    await user.click(screen.getByRole("button", { name: /Edit card/ }));
    await screen.findByRole("dialog");
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Edit card/ }));
    const dialog = await screen.findByRole("dialog");
    const text = await within(dialog).findByLabelText("Text");
    await user.clear(text);
    await user.type(text, "{{{{c1::a}}");
    await user.click(within(dialog).getByRole("button", { name: "Save changes" }));
    expect(await screen.findByText("Card not found.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Skip this card" })).toBeInTheDocument();
  });

  it("shows a note that cannot load in the editor", async () => {
    mockApi({
      ...me,
      "GET /decks/d1/review": { body: [card("c1")] },
      "GET /cards/c1": { body: basic("c1", "Q", "A") },
      "GET /notes/n-c1": { status: 500 },
    });
    renderApp("/decks/d1/study");
    const user = userEvent.setup();
    await screen.findByText("Q");
    await user.click(screen.getByRole("button", { name: /Edit card/ }));
    expect(await within(await screen.findByRole("dialog")).findByRole("alert")).toHaveTextContent(
      "Something went wrong on the server",
    );
  });

  it("holds answers while an undo is on its way, and keeps the undo after a failure in transit", async () => {
    let answerUndo: (reply: { body: unknown }) => void = () => undefined;
    let undoCalls = 0;
    const calls = mockApi({
      ...me,
      "GET /decks/d1/review": { body: [card("c1"), card("c2")] },
      "GET /cards/c1": { body: basic("c1", "Q1", "A1") },
      "GET /cards/c2": { body: basic("c2", "Q2", "A2") },
      "POST /cards/c1/review": { body: { review_id: "r1" } },
      "POST /reviews/r1/undo": () => {
        undoCalls += 1;
        return undoCalls === 1
          ? { status: 503 }
          : new Promise((resolve) => {
              answerUndo = resolve;
            });
      },
    });
    renderApp("/decks/d1/study");
    const user = userEvent.setup();
    await screen.findByText("Q1");
    await user.keyboard(" 3");
    await screen.findByText("Q2");
    await user.click(await screen.findByRole("button", { name: /Undo last answer/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong on the server");
    await user.keyboard(" ");
    await user.click(await screen.findByRole("button", { name: /Undo last answer/ }));
    expect(screen.getByRole("button", { name: /Good/ })).toBeDisabled();
    await user.keyboard("3");
    expect(calls.filter((c) => c.path === "/cards/c2/review")).toHaveLength(0);
    answerUndo({ body: { review_id: "r1", card_id: "c1", schedule: null, daily_goal: goal(0) } });
    expect(await screen.findByText("Q1")).toBeInTheDocument();
    expect(screen.getByText("Card 1 of 2")).toBeInTheDocument();
  });
});
