// SPDX-License-Identifier: AGPL-3.0-only
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { suggestedRating, type Evaluation } from "../api/aiStudy";
import type { Card, CardDetail } from "../api/types";
import { USER, mockApi, renderApp, type Call } from "../test/helpers";

const card = (id: string): Card => ({
  id,
  deck_id: "d1",
  note_id: `n-${id}`,
  ordinal: 0,
  suspended: false,
  flag: 0,
  buried_until: null,
});

const basic = (id: string, front: string, back: string): CardDetail => ({
  ...card(id),
  note_type: "basic",
  fields: { front, back },
  tags: [],
  owned: true,
});

const me = { "GET /auth/me": { body: USER } };

const session = {
  ...me,
  "GET /decks/d1/review": { body: [card("c1"), card("c2")] },
  "GET /cards/c1": { body: basic("c1", "What is ATP?", "The energy currency") },
  "GET /cards/c2": { body: basic("c2", "What is osmosis?", "Water moving across a membrane") },
  "POST /cards/c1/review": { body: { review_id: "r1" } },
  "POST /cards/c2/review": { body: { review_id: "r2" } },
};

const graded = (over: Partial<Evaluation> = {}): Evaluation => ({
  verdict: "correct",
  score: 0.8,
  explanation: "You named the **role**. <img src=x onerror=alert(1)> [Review](https://evil.example/e)",
  ...over,
});

function reviews(calls: Call[]): unknown[] {
  return calls.filter((c) => c.method === "POST" && c.path.endsWith("/review")).map((c) => c.body);
}

async function useAiMode(user: ReturnType<typeof userEvent.setup>): Promise<HTMLElement> {
  await screen.findByText("What is ATP?");
  await user.click(screen.getByLabelText("Typing it (AI checks)"));
  const box = await screen.findByLabelText("Your answer");
  await waitFor(() => {
    expect(box).toHaveFocus();
  });
  return box;
}

describe("AI-evaluation study mode", () => {
  it("checks a typed answer, explains the gap, and sends the confirmed rating", async () => {
    const calls = mockApi({ ...session, "POST /cards/c1/evaluate": { body: graded() } });
    renderApp("/decks/d1/study");
    const user = userEvent.setup();
    const box = await useAiMode(user);
    expect(screen.queryByRole("button", { name: /Show answer/ })).toHaveTextContent("Show answer without checking");
    expect(screen.getByRole("button", { name: "Check answer" })).toBeDisabled();

    await user.type(box, "It stores energy{Shift>}{Enter}{/Shift}for cells{Enter}");
    expect(await screen.findByText("The energy currency")).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/cards/c1/evaluate")?.body).toEqual({
      answer: "It stores energy\nfor cells",
    });
    expect(screen.getByText("AI evaluation. You choose the final rating.")).toBeInTheDocument();
    expect(screen.getByText("Correct")).toBeInTheDocument();
    expect(screen.getByText("(80%)")).toBeInTheDocument();
    expect(screen.getByText("role")).toBeInTheDocument();
    expect(document.querySelector("img[onerror]")).toBeNull();
    const evaluation = document.querySelector(".evaluation") as HTMLElement;
    expect(evaluation.querySelector("a")).toBeNull();
    expect(evaluation).toHaveTextContent("Review (https://evil.example/e)");
    expect(screen.getByText(/Checked: Correct\. Suggested rating: Good\./)).toBeInTheDocument();
    const good = screen.getByRole("button", { name: /Good/ });
    expect(good).toHaveTextContent("(suggested)");
    expect(good).toHaveAttribute("aria-keyshortcuts", "3 Enter");
    expect(screen.getByRole("group", { name: "How well did you know it?" })).toHaveFocus();

    await user.keyboard("{Enter}");
    expect(await screen.findByText("What is osmosis?")).toBeInTheDocument();
    expect(reviews(calls)).toEqual([{ rating: 3, idempotency_key: expect.any(String) as string }]);
    // The mode stays on for the next card, and for the next session.
    await waitFor(() => {
      expect(screen.getByLabelText("Your answer")).toHaveFocus();
    });
    expect(screen.getByLabelText("Your answer")).toHaveValue("");
    expect(localStorage.getItem("flashcards.studyMode")).toBe("ai");
  });

  it("lets the student change the suggested rating", async () => {
    const calls = mockApi({
      ...session,
      "POST /cards/c1/evaluate": { body: graded({ verdict: "incorrect", score: 0.1 }) },
    });
    renderApp("/decks/d1/study");
    const user = userEvent.setup();
    const box = await useAiMode(user);
    await user.type(box, "No idea");
    await user.click(screen.getByRole("button", { name: "Check answer" }));
    expect(await screen.findByText("Not correct")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Again/ })).toHaveTextContent("(suggested)");
    await user.keyboard("2");
    await screen.findByText("What is osmosis?");
    expect(reviews(calls)).toEqual([{ rating: 2, idempotency_key: expect.any(String) as string }]);
  });

  it("shows the answer without asking the AI", async () => {
    const calls = mockApi(session);
    renderApp("/decks/d1/study");
    const user = userEvent.setup();
    await useAiMode(user);
    await user.click(screen.getByRole("button", { name: "Show answer without checking" }));
    expect(await screen.findByText("The energy currency")).toBeInTheDocument();
    expect(screen.queryByText("(suggested)")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Good/ })).toHaveClass("primary");
    expect(screen.queryByLabelText("Your answer")).not.toBeInTheDocument();
    await user.keyboard("{Enter}"); // no suggestion: Enter does not rate
    expect(reviews(calls)).toHaveLength(0);
    await user.keyboard("4");
    await screen.findByText("What is osmosis?");
    expect(calls.some((c) => c.path.endsWith("/evaluate"))).toBe(false);
  });

  it("keeps the typed answer when the AI fails, and can reveal while it is still checking", async () => {
    let attempt = 0;
    let release: (() => void) | null = null;
    mockApi({
      ...session,
      "POST /cards/c1/evaluate": () => {
        attempt += 1;
        if (attempt === 1) {
          return { status: 429, body: { detail: "Daily AI request limit reached." } };
        }
        if (attempt === 2) {
          return { status: 502, body: { detail: "x" } };
        }
        return new Promise((resolve) => {
          release = () => {
            resolve({ body: graded({ verdict: "partial", score: 0.5 }) });
          };
        });
      },
    });
    renderApp("/decks/d1/study");
    const user = userEvent.setup();
    const box = await useAiMode(user);
    await user.type(box, "Energy{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("Daily AI request limit reached. Everything else");
    expect(screen.getByLabelText("Your answer")).toHaveValue("Energy");
    await user.keyboard("{Enter}");
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("could not be used");
    });
    await user.click(screen.getByRole("button", { name: "Check answer" }));
    expect(await screen.findByText("The AI is checking your answer…")).toBeInTheDocument();
    expect(screen.getByLabelText("Your answer")).toHaveAttribute("readonly");
    expect(screen.getByRole("button", { name: "Checking…" })).toBeDisabled();
    await user.keyboard("{Enter}"); // already checking: nothing more is sent
    await user.click(screen.getByRole("button", { name: "Show answer now" }));
    expect(await screen.findByText("The energy currency")).toBeInTheDocument();
    expect(screen.getByText("Energy")).toBeInTheDocument();
    expect(screen.queryByText("(suggested)")).not.toBeInTheDocument();
    (release as unknown as () => void)();
    expect(await screen.findByText("Partly correct")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Hard/ })).toHaveTextContent("(suggested)");
  });

  it("keeps undo working: Escape leaves the box and U takes the answer back", async () => {
    const calls = mockApi({
      ...session,
      "POST /cards/c1/evaluate": { body: graded() },
      "POST /reviews/r1/undo": { body: { review_id: "r1", card_id: "c1", schedule: null, daily_goal: null } },
    });
    renderApp("/decks/d1/study");
    const user = userEvent.setup();
    const box = await useAiMode(user);
    await user.type(box, "Energy{Enter}");
    await screen.findByText("(suggested)");
    await user.keyboard("{Enter}");
    await screen.findByText("What is osmosis?");
    await screen.findByRole("button", { name: /Undo last answer/ });
    const next = screen.getByLabelText("Your answer");
    await waitFor(() => {
      expect(next).toHaveFocus();
    });
    await user.keyboard("{Escape}");
    expect(next).not.toHaveFocus();
    await user.keyboard("u");
    expect(await screen.findByText("What is ATP?")).toBeInTheDocument();
    expect(calls.filter((c) => c.path === "/reviews/r1/undo")).toHaveLength(1);
    expect(screen.queryByText("(suggested)")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Your answer")).toHaveValue("");
  });

  it("starts in the saved mode and switches back to revealing", async () => {
    localStorage.setItem("flashcards.studyMode", "ai");
    mockApi(session);
    renderApp("/decks/d1/study");
    const user = userEvent.setup();
    expect(await screen.findByLabelText("Your answer")).toBeInTheDocument();
    await user.click(screen.getByLabelText("Revealing it"));
    expect(screen.queryByLabelText("Your answer")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Show answer/ })).toBeInTheDocument();
    expect(screen.getByRole("article")).toHaveFocus();
    expect(localStorage.getItem("flashcards.studyMode")).toBe("self");
  });

  it("keeps the rating keys working while the mode radios have focus", async () => {
    const calls = mockApi(session);
    renderApp("/decks/d1/study");
    const user = userEvent.setup();
    await screen.findByText("What is ATP?");
    await user.keyboard(" ");
    await screen.findByText("The energy currency");
    await user.click(screen.getByLabelText("Revealing it"));
    expect(screen.getByLabelText("Revealing it")).toHaveFocus();
    await user.keyboard("4");
    await screen.findByText("What is osmosis?");
    expect(reviews(calls)).toEqual([{ rating: 4, idempotency_key: expect.any(String) as string }]);
  });

  it("works when storage is blocked", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    mockApi(session);
    renderApp("/decks/d1/study");
    const user = userEvent.setup();
    expect(await screen.findByLabelText("Revealing it")).toBeChecked();
    await user.click(screen.getByLabelText("Typing it (AI checks)"));
    expect(await screen.findByLabelText("Your answer")).toBeInTheDocument();
  });

  it("keeps the typed answer on screen when the answer is shown without checking", async () => {
    mockApi(session);
    renderApp("/decks/d1/study");
    const user = userEvent.setup();
    const box = await useAiMode(user);
    await user.type(box, "My guess{Escape}");
    await user.keyboard(" ");
    expect(await screen.findByText("The energy currency")).toBeInTheDocument();
    expect(screen.getByText("My guess")).toBeInTheDocument();
  });

  it("drops an evaluation that answers after the session moved on", async () => {
    let release: (() => void) | null = null;
    mockApi({
      ...session,
      "POST /cards/c1/evaluate": () =>
        new Promise((resolve) => {
          release = () => {
            resolve({ body: graded() });
          };
        }),
    });
    renderApp("/decks/d1/study");
    const user = userEvent.setup();
    const box = await useAiMode(user);
    await user.type(box, "Energy{Enter}");
    await user.click(await screen.findByRole("button", { name: "Show answer now" }));
    await user.keyboard("3");
    expect(await screen.findByText("What is osmosis?")).toBeInTheDocument();
    (release as unknown as () => void)();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(screen.queryByText("(suggested)")).not.toBeInTheDocument();
    expect(screen.queryByText("Water moving across a membrane")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Your answer")).toHaveValue("");
  });

  it("ignores a request that was aborted", async () => {
    mockApi({
      ...session,
      "POST /cards/c1/evaluate": () => {
        throw new DOMException("Aborted", "AbortError");
      },
    });
    renderApp("/decks/d1/study");
    const user = userEvent.setup();
    const box = await useAiMode(user);
    await user.type(box, "Energy{Enter}");
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText("The AI is checking your answer…")).toBeInTheDocument();
  });

  it("suggests a rating from the verdict and score", () => {
    expect(suggestedRating(graded({ verdict: "correct", score: 0.95 }))).toBe(4);
    expect(suggestedRating(graded({ verdict: "correct", score: 0.9 }))).toBe(3);
    expect(suggestedRating(graded({ verdict: "partial" }))).toBe(2);
    expect(suggestedRating(graded({ verdict: "incorrect" }))).toBe(1);
  });
});
