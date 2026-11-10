// SPDX-License-Identifier: AGPL-3.0-only
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { Deck, DeckStats } from "../api/types";
import { AuthProvider } from "../auth/AuthContext";
import { OnboardingPage } from "./OnboardingPage";
import { OnboardingPrompt } from "./OnboardingPrompt";
import { USER, mockApi, renderApp } from "../test/helpers";
import {
  finishOnboarding,
  forgetOnboarding,
  nextStep,
  readOnboarding,
  resetOnboardingForTests,
  saveOnboarding,
  startOnboarding,
  stepNumber,
} from "./state";

const me = { "GET /auth/me": { body: USER } };
const KEY = `flashcards.onboarding.${USER.id}`;

const deck = (id: string, name: string): Deck => ({
  id,
  name,
  parent_id: null,
  created_at: "2026-01-01T00:00:00Z",
});

const EMPTY_STATS: DeckStats = {
  total: 0,
  new: 0,
  learning: 0,
  review: 0,
  mature: 0,
  suspended: 0,
  due: 0,
  proficiency: 0,
};

describe("onboarding state", () => {
  it("is null until started, then remembers the step per user", () => {
    expect(readOnboarding("u1")).toBeNull();
    startOnboarding("u1");
    saveOnboarding("u1", { status: "active", step: "goal", deckId: "d1" });
    resetOnboardingForTests(); // a new page load reads it back from storage
    expect(readOnboarding("u1")).toEqual({ status: "active", step: "goal", deckId: "d1" });
    expect(readOnboarding("u2")).toBeNull();
    finishOnboarding("u1");
    expect(readOnboarding("u1")).toEqual({ status: "done", step: "goal", deckId: "d1" });
    forgetOnboarding("u1");
    expect(readOnboarding("u1")).toBeNull();
    expect(localStorage.getItem("flashcards.onboarding.u1")).toBeNull();
  });

  it("treats anything malformed in storage as never started", () => {
    for (const raw of [
      "not json",
      "null",
      "42",
      JSON.stringify({ status: "active", step: "elsewhere" }),
      JSON.stringify({ status: "paused", step: "deck" }),
    ]) {
      localStorage.setItem("flashcards.onboarding.u1", raw);
      expect(readOnboarding("u1")).toBeNull();
    }
    localStorage.setItem("flashcards.onboarding.u1", JSON.stringify({ status: "done", step: "study", deckId: 7 }));
    expect(readOnboarding("u1")).toEqual({ status: "done", step: "study", deckId: null });
  });

  it("works for the visit when storage is unavailable", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(readOnboarding("u1")).toBeNull();
    finishOnboarding("u1");
    expect(readOnboarding("u1")?.status).toBe("done");
    forgetOnboarding("u1");
    expect(readOnboarding("u1")).toBeNull();
  });

  it("numbers the steps and knows the last one", () => {
    expect(stepNumber("deck")).toBe(1);
    expect(stepNumber("study")).toBe(5);
    expect(nextStep("goal")).toBe("study");
    expect(nextStep("study")).toBeNull();
  });
});

describe("guided first run", () => {
  it("walks a new user from a deck to a first session", async () => {
    let decks: Deck[] = [];
    const calls = mockApi({
      ...me,
      "GET /decks": () => ({ body: decks }),
      "POST /decks": () => {
        decks = [deck("d1", "Biology")];
        return { status: 201, body: decks[0] };
      },
      "POST /goals": { status: 201, body: { id: "g1" } },
      "GET /decks/d1/review": { body: [] },
      "GET /decks/d1/stats": { body: EMPTY_STATS },
    });
    renderApp("/");
    expect(await screen.findByRole("heading", { name: "Getting started" })).toBeInTheDocument();
    expect(screen.getByText("Step 1 of 5")).toBeInTheDocument();
    const user = userEvent.setup();

    await user.type(screen.getByLabelText("Deck name"), "Biology");
    await user.click(screen.getByRole("button", { name: "Create deck" }));
    expect(await screen.findByRole("heading", { name: "Add material" })).toHaveFocus();
    expect(screen.getByRole("link", { name: "Generate cards from a PDF or image" })).toHaveAttribute(
      "href",
      "/decks/d1/generate",
    );
    expect(screen.getByRole("link", { name: "Write a card yourself" })).toHaveAttribute("href", "/decks/d1/notes/new");

    await user.click(screen.getByRole("button", { name: "I have added cards" }));
    expect(await screen.findByRole("heading", { name: "Check your cards" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open the deck" })).toHaveAttribute("href", "/decks/d1");

    await user.click(screen.getByRole("button", { name: "My cards look right" }));
    expect(await screen.findByRole("heading", { name: "Set your exam" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save my exam" })).toBeDisabled();
    await user.type(screen.getByLabelText("Exam date"), "2099-06-30");
    fireEvent.change(screen.getByLabelText(/Target grade/), { target: { value: "90" } });
    expect(screen.getByLabelText("Target grade: 90%")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save my exam" }));
    expect(await screen.findByRole("heading", { name: "First session" })).toBeInTheDocument();
    const goal = calls.find((c) => c.method === "POST" && c.path === "/goals")?.body as Record<string, unknown>;
    expect(goal).toMatchObject({ name: "Biology exam", deck_ids: ["d1"], desired_grade: 0.9 });
    expect(goal.exam_date).toBe(new Date("2099-06-30T00:00:00").toISOString());

    await user.click(screen.getByRole("button", { name: "Start my first session" }));
    await waitFor(() => {
      expect(readOnboarding(USER.id)?.status).toBe("done");
    });
    expect((await screen.findAllByText(/Nothing is due|no cards|All done|caught up/i)).length).toBeGreaterThan(0);
  });

  it("can skip every step and is then remembered as done", async () => {
    mockApi({ ...me, "GET /decks": { body: [] } });
    renderApp("/welcome");
    const user = userEvent.setup();
    expect(await screen.findByRole("heading", { name: "Start a deck" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Skip this step" }));
    expect(await screen.findByText("Start a deck first to generate or write cards for it.")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Write a card yourself" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Skip this step" }));
    expect(await screen.findByRole("heading", { name: "Check your cards" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Open the deck" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Skip this step" }));
    expect(await screen.findByRole("link", { name: "goals page" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Skip this step" }));
    expect(await screen.findByRole("heading", { name: "First session" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start my first session" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Skip this step" }));
    // Back home with no decks, but the setup is done: no second redirect.
    expect(await screen.findByText(/No decks yet/)).toBeInTheDocument();
    expect(readOnboarding(USER.id)?.status).toBe("done");
  });

  it("finishes from the last step without a session", async () => {
    saveOnboarding(USER.id, { status: "active", step: "study", deckId: null });
    mockApi({ ...me, "GET /decks": { body: [deck("d1", "Biology")] } });
    renderApp("/welcome");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Finish setup" }));
    expect(await screen.findByRole("heading", { name: "Decks" })).toBeInTheDocument();
    expect(readOnboarding(USER.id)?.status).toBe("done");
  });

  it("can skip the whole setup at once", async () => {
    mockApi({ ...me, "GET /decks": { body: [] } });
    renderApp("/");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Skip the setup" }));
    expect(await screen.findByText(/No decks yet/)).toBeInTheDocument();
    expect(screen.queryByRole("complementary", { name: "Guided setup" })).not.toBeInTheDocument();
    expect(JSON.parse(localStorage.getItem(KEY) ?? "{}")).toMatchObject({ status: "done" });
  });

  it("uses a deck the user already has, and forgets one deleted since", async () => {
    mockApi({ ...me, "GET /decks": { body: [deck("d1", "Biology"), deck("d2", "Chemistry")] } });
    renderApp("/welcome");
    const user = userEvent.setup();
    const useDeck = await screen.findByRole("button", { name: "Use this deck" });
    expect(useDeck).toBeDisabled();
    await user.selectOptions(screen.getByLabelText("Or use a deck you already have"), "d2");
    await user.click(useDeck);
    expect(await screen.findByRole("link", { name: "Write a card yourself" })).toHaveAttribute(
      "href",
      "/decks/d2/notes/new",
    );
    expect(readOnboarding(USER.id)?.deckId).toBe("d2");
  });

  it("treats a chosen deck that no longer exists as none", async () => {
    saveOnboarding(USER.id, { status: "active", step: "verify", deckId: "gone" });
    mockApi({ ...me, "GET /decks": { body: [deck("d1", "Biology")] } });
    renderApp("/welcome");
    expect(await screen.findByRole("heading", { name: "Check your cards" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Open the deck" })).not.toBeInTheDocument();
  });

  it("comes back to the next step after leaving to add material", async () => {
    saveOnboarding(USER.id, { status: "active", step: "material", deckId: "d1" });
    mockApi({ ...me, "GET /decks": { body: [deck("d1", "Biology")] }, "GET /note-types": { body: [] } });
    renderApp("/welcome");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("link", { name: "Import a deck from Anki" }));
    expect(await screen.findByRole("heading", { name: /Import/ })).toBeInTheDocument();
    expect(readOnboarding(USER.id)?.step).toBe("verify");
  });

  it("offers a setup in progress on the home page until it is skipped", async () => {
    saveOnboarding(USER.id, { status: "active", step: "material", deckId: "d1" });
    mockApi({ ...me, "GET /decks": { body: [deck("d1", "Biology")] } });
    renderApp("/");
    expect(await screen.findByText("You are on step 2 of 5 of the guided setup.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Continue setup" })).toHaveAttribute("href", "/welcome");
    await userEvent.setup().click(screen.getByRole("button", { name: "Skip the setup" }));
    expect(screen.queryByText(/of the guided setup/)).not.toBeInTheDocument();
    expect(readOnboarding(USER.id)?.status).toBe("done");
  });

  it("never sends a user who has decks to the setup by surprise", async () => {
    mockApi({ ...me, "GET /decks": { body: [deck("d1", "Biology")] } });
    renderApp("/");
    expect(await screen.findByRole("link", { name: "Biology" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Getting started" })).not.toBeInTheDocument();
    expect(readOnboarding(USER.id)).toBeNull();
  });

  it("shows why a goal could not be saved and why the decks could not load", async () => {
    saveOnboarding(USER.id, { status: "active", step: "goal", deckId: "d1" });
    mockApi({
      ...me,
      "GET /decks": { body: [deck("d1", "Biology")] },
      "POST /goals": { status: 422, body: { detail: "exam_date must be in the future" } },
    });
    const { unmount } = renderApp("/welcome");
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Exam date"), "2099-01-01");
    await user.click(screen.getByRole("button", { name: "Save my exam" }));
    expect(await screen.findByText("exam_date must be in the future")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Set your exam" })).toBeInTheDocument();
    unmount();

    mockApi({ ...me, "GET /decks": { status: 500 } });
    renderApp("/welcome");
    expect(await screen.findByRole("alert")).toHaveTextContent(/server/);
  });

  it("can be run again from the account page", async () => {
    finishOnboarding(USER.id);
    mockApi({
      ...me,
      "GET /decks": { body: [deck("d1", "Biology")] },
      "GET /auth/sessions": { body: [] },
      "GET /account/ai-quota": {
        body: { period: "2026-11", monthly_pages: 10, used_pages: 0, max_document_pages: 300, quota_applies: true },
      },
      "GET /legal/ai-disclosure": {
        body: { ai_enabled: false, trains_on_user_content: false, statements: [], provider: null },
      },
    });
    renderApp("/account");
    await userEvent.setup().click(await screen.findByRole("button", { name: "Run the guided setup" }));
    expect(await screen.findByRole("heading", { name: "Start a deck" })).toBeInTheDocument();
    expect(readOnboarding(USER.id)).toEqual({ status: "active", step: "deck", deckId: null });
  });

  it("says it is finished when opened again, and starts over only on request", async () => {
    saveOnboarding(USER.id, { status: "done", step: "study", deckId: "d1" });
    mockApi({ ...me, "GET /decks": { body: [] } });
    renderApp("/welcome");
    expect(await screen.findByText("You have finished the guided setup.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to your decks" })).toHaveAttribute("href", "/");
    expect(readOnboarding(USER.id)?.status).toBe("done");
    await userEvent.setup().click(screen.getByRole("button", { name: "Run it again" }));
    expect(await screen.findByText("Step 1 of 5")).toBeInTheDocument();
    expect(readOnboarding(USER.id)?.status).toBe("active");
  });
});

describe("without a signed-in user", () => {
  it("renders nothing and goes nowhere", async () => {
    const calls = mockApi({ "GET /auth/me": { status: 401, body: { detail: "x" } } });
    const { container } = render(
      <AuthProvider>
        <MemoryRouter>
          <OnboardingPrompt deckCount={0} />
          <OnboardingPage />
        </MemoryRouter>
      </AuthProvider>,
    );
    await waitFor(() => {
      expect(calls.some((c) => c.path === "/auth/me")).toBe(true);
    });
    expect(container).toBeEmptyDOMElement();
    expect(readOnboarding("")).toBeNull();
  });
});

describe("empty states teach the next step", () => {
  it("an empty deck points at the ways to add cards", async () => {
    mockApi({ ...me, "GET /decks": { body: [deck("d1", "Biology")] }, "GET /decks/d1/stats": { body: EMPTY_STATS } });
    renderApp("/decks/d1");
    expect(await screen.findByText(/This deck has no cards yet/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "import an Anki deck" })).toHaveAttribute("href", "/import");
  });
});
