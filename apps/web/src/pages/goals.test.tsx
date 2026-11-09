// SPDX-License-Identifier: AGPL-3.0-only
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { PlanReport } from "../api/studyProgress";
import type { Deck, Goal } from "../api/types";
import { USER, mockApi, renderApp } from "../test/helpers";
import { goalChanges, gradeFromRetention, localDate, tomorrow } from "./GoalsPage";

const me = { "GET /auth/me": { body: USER } };

const deck = (id: string, name: string): Deck => ({ id, name, parent_id: null, created_at: "2026-01-01T00:00:00Z" });

const GOAL: Goal = {
  id: "g1",
  name: "Biology final",
  deck_ids: ["d1"],
  exam_date: "2026-12-15T00:00:00Z",
  target_retention: 0.885,
  created_at: "2026-11-01T00:00:00Z",
};

const PLAN: PlanReport = {
  days_until_exam: 37,
  total_cards: 120,
  new_cards: 40,
  scheduled_cards: 80,
  new_per_day: 2,
  reviews_per_day: 15,
  readiness: 0.62,
  on_track: true,
};

describe("goal helpers", () => {
  it("maps retention back to the grade slider and dates to the local calendar", () => {
    expect(gradeFromRetention(0.8)).toBe(0);
    expect(gradeFromRetention(0.97)).toBe(100);
    expect(gradeFromRetention(0.5)).toBe(0);
    expect(gradeFromRetention(1)).toBe(100);
    expect(localDate(new Date(2026, 0, 5, 12).toISOString())).toBe("2026-01-05");
    expect(tomorrow(new Date(2026, 11, 31, 23, 30))).toBe("2027-01-01");
  });

  it("sends only what an edit changed", () => {
    const same = { name: GOAL.name, deck_ids: ["d1"], exam_date: GOAL.exam_date, desired_grade: 0.5 };
    expect(goalChanges(GOAL, same)).toEqual({});
    expect(
      goalChanges(GOAL, { name: "X", deck_ids: ["d2", "d1"], exam_date: "2027-03-01T12:00:00Z", desired_grade: 0.9 }),
    ).toEqual({ name: "X", deck_ids: ["d2", "d1"], exam_date: "2027-03-01T12:00:00Z", desired_grade: 0.9 });
  });
});

describe("goals", () => {
  it("lists goals and creates one from decks, an exam date and a target grade", async () => {
    const calls = mockApi({
      ...me,
      "GET /goals": { body: [GOAL] },
      "GET /decks": { body: [deck("d1", "Biology"), deck("d2", "Chemistry")] },
      "POST /goals": { status: 201, body: { ...GOAL, id: "g2", name: "Chem" } },
      "GET /goals/g2": { body: { ...GOAL, id: "g2", name: "Chem" } },
      "GET /goals/g2/plan": { body: PLAN },
    });
    renderApp("/goals");
    const user = userEvent.setup();
    expect(await screen.findByRole("link", { name: "Biology final" })).toHaveAttribute("href", "/goals/g1");
    await waitFor(() => {
      expect(document.title).toBe("Goals · Flash-Cards");
    });
    const create = await screen.findByRole("button", { name: "Create goal" });
    expect(create).toBeDisabled();
    await user.type(screen.getByLabelText("Goal name"), " Chem ");
    await user.click(screen.getByLabelText("Chemistry"));
    await user.click(screen.getByLabelText("Biology"));
    await user.click(screen.getByLabelText("Biology"));
    fireEvent.change(screen.getByLabelText("Exam date"), { target: { value: "2026-12-20" } });
    const slider = screen.getByLabelText(/Target grade/);
    fireEvent.change(slider, { target: { value: "80" } });
    expect(screen.getByText("Target grade: 80%")).toBeInTheDocument();
    expect(screen.getByText(/keep about 94% of cards/)).toBeInTheDocument();
    await user.click(create);
    expect(await screen.findByRole("heading", { name: "Chem" })).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({
      name: "Chem",
      deck_ids: ["d2"],
      exam_date: new Date("2026-12-20T00:00:00").toISOString(),
      desired_grade: 0.8,
    });
  });

  it("says when there are no goals or decks, and shows a list that cannot load", async () => {
    mockApi({ ...me, "GET /goals": { body: [] }, "GET /decks": { body: [] } });
    const { unmount } = renderApp("/goals");
    expect(await screen.findByText(/No goals yet/)).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "decks page" })).toBeInTheDocument();
    unmount();
    mockApi({ ...me, "GET /goals": { status: 500 }, "GET /decks": { status: 500 } });
    renderApp("/goals");
    await waitFor(() => {
      expect(screen.getAllByRole("alert")).toHaveLength(2);
    });
  });

  it("shows a goal's plan and readiness, and what changed since the plan was accepted", async () => {
    let accepted = false;
    const calls = mockApi({
      ...me,
      "GET /goals/g1": { body: GOAL },
      "GET /decks": { body: [deck("d1", "Biology")] },
      "GET /goals/g1/plan": () => ({
        body: accepted
          ? { ...PLAN, changes: { ...changes, changed: false } }
          : { ...PLAN, on_track: false, changes },
      }),
      "POST /goals/g1/plan/accept": () => {
        accepted = true;
        return { body: PLAN };
      },
    });
    const changes = {
      since: "2026-11-01T10:00:00Z",
      changed: true,
      daily_load_before: 12,
      daily_load_after: 1,
      before: PLAN,
      reasons: [
        { reason: "cards_added", count: 5 },
        { reason: "cards_removed", count: 1 },
        { reason: "cards_started", count: 3 },
        { reason: "missed_days", count: 2 },
        { reason: "days_passed", count: 1 },
        { reason: "exam_date_changed", count: 0 },
        { reason: "target_changed", count: 0 },
      ],
    };
    renderApp("/goals/g1");
    const user = userEvent.setup();
    expect(await screen.findByRole("heading", { name: "Biology final" })).toBeInTheDocument();
    expect(await screen.findByText(/Study 2 new cards and about 15 reviews a day/)).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Readiness 62%" })).toBeInTheDocument();
    expect(screen.getByText(/Behind:/)).toBeInTheDocument();
    expect(await screen.findByText(/Target grade 50%\. Biology\./)).toBeInTheDocument();
    const panel = screen.getByRole("region", { name: /What changed since/ });
    expect(within(panel).getByText("Daily load: 12 cards before, 1 card now.")).toBeInTheDocument();
    for (const text of [
      "5 cards added to the goal's decks.",
      "1 card removed or suspended.",
      "3 cards studied for the first time.",
      "2 days without reviews.",
      "1 day closer to the exam.",
      "The exam date changed.",
      "The target grade changed.",
    ]) {
      expect(within(panel).getByText(text)).toBeInTheDocument();
    }
    await user.click(within(panel).getByRole("button", { name: "Accept the new plan" }));
    await waitFor(() => {
      expect(screen.queryByRole("region", { name: /What changed since/ })).not.toBeInTheDocument();
    });
    expect(await screen.findByText(/On track/)).toBeInTheDocument();
    expect(calls.filter((c) => c.path === "/goals/g1/plan/accept")).toHaveLength(1);
    await waitFor(() => {
      expect(document.title).toBe("Biology final · Flash-Cards");
    });
  });

  it("works with a plan that carries no changes, and reports an accept that fails", async () => {
    mockApi({
      ...me,
      "GET /goals/g1": { body: { ...GOAL, deck_ids: ["gone"] } },
      "GET /decks": { body: [] },
      "GET /goals/g1/plan": {
        body: {
          ...PLAN,
          new_per_day: 1,
          reviews_per_day: 1,
          changes: { since: "2026-11-01T10:00:00Z", changed: true, daily_load_before: 1, daily_load_after: 2, before: PLAN, reasons: [] },
        },
      },
      "POST /goals/g1/plan/accept": { status: 500 },
    });
    renderApp("/goals/g1");
    const user = userEvent.setup();
    expect(await screen.findByText(/Study 1 new card and about 1 review a day/)).toBeInTheDocument();
    expect(await screen.findByText(/A deck\./)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Accept the new plan" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong on the server");
  });

  it("edits and deletes a goal", async () => {
    const calls = mockApi({
      ...me,
      "GET /goals/g1": { body: GOAL },
      "GET /decks": { body: [deck("d1", "Biology")] },
      "GET /goals/g1/plan": { body: PLAN },
      "PATCH /goals/g1": { body: { ...GOAL, name: "Bio exam" } },
      "DELETE /goals/g1": { status: 204 },
      "GET /goals": { body: [] },
    });
    renderApp("/goals/g1");
    const user = userEvent.setup();
    await screen.findByText(/Study 2 new cards/);
    const edit = await screen.findByRole("button", { name: "Edit goal" });
    await waitFor(() => {
      expect(edit).toBeEnabled();
    });
    await user.click(edit);
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await user.click(screen.getByRole("button", { name: "Edit goal" }));
    const name = screen.getByLabelText("Goal name");
    expect(screen.getByLabelText(/Target grade/)).toHaveValue("50");
    expect(screen.getByLabelText("Exam date")).toHaveValue(localDate(GOAL.exam_date));
    await user.clear(name);
    await user.type(name, "Bio exam");
    await user.click(screen.getByRole("button", { name: "Save goal" }));
    expect(await screen.findByRole("heading", { name: "Bio exam" })).toBeInTheDocument();
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ name: "Bio exam" });
    // Saving without a change sends nothing.
    await user.click(screen.getByRole("button", { name: "Edit goal" }));
    await user.click(screen.getByRole("button", { name: "Save goal" }));
    expect(await screen.findByRole("button", { name: "Edit goal" })).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "Delete goal" }));
    await user.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete goal" }));
    expect(await screen.findByText(/No goals yet/)).toBeInTheDocument();
  });

  it("reports a goal that cannot load, a plan that cannot load and a failed delete", async () => {
    mockApi({ ...me, "GET /goals/g1": { status: 404, body: { detail: "Goal not found." } } });
    const { unmount } = renderApp("/goals/g1");
    expect(await screen.findByRole("alert")).toHaveTextContent("Goal not found.");
    unmount();
    mockApi({
      ...me,
      "GET /goals/g1": { body: GOAL },
      "GET /decks": { body: [] },
      "GET /goals/g1/plan": { status: 500 },
      "DELETE /goals/g1": { status: 500 },
    });
    renderApp("/goals/g1");
    const user = userEvent.setup();
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong");
    await user.click(screen.getByRole("button", { name: "Delete goal" }));
    await user.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete goal" }));
    await waitFor(() => {
      expect(screen.getAllByRole("alert")).toHaveLength(2);
    });
  });
});
