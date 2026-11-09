// SPDX-License-Identifier: AGPL-3.0-only
import { describe, expect, it } from "vitest";

import { mockApi } from "../test/helpers";
import { ai, auth, browserParams, cards, decks, notes } from "./endpoints";

describe("browserParams", () => {
  it("repeats list values and skips empty ones", () => {
    expect(browserParams({ state: ["new", "review"], tag: "", q: "cell", limit: 50 })).toBe(
      "state=new&state=review&q=cell&limit=50",
    );
  });
});

describe("endpoints", () => {
  it("map to the API routes", async () => {
    const calls = mockApi({});
    const file = new File(["x"], "a.pdf", { type: "application/pdf" });
    const all = [
      auth.me(),
      auth.login("a@b.c", "pw"),
      auth.signup("t", "a@b.c", "pw"),
      auth.logout(),
      auth.verifyEmail("t"),
      auth.resendVerification(),
      auth.requestPasswordReset("a@b.c"),
      auth.confirmPasswordReset("t", "pw"),
      decks.list(),
      decks.get("d/1"),
      decks.create("A", null),
      decks.rename("d1", "B"),
      decks.remove("d1"),
      decks.stats("d1"),
      decks.dueQueue("d1"),
      cards.get("c1"),
      cards.review("c1", 3, "k1"),
      cards.browse({ deck_id: "d1" }),
      cards.bulk({ card_ids: ["c1"], action: "suspend" }),
      notes.create("d1", "basic", { front: "f", back: "b" }, ["t"]),
      notes.get("n1"),
      notes.update("n1", { front: "f" }, []),
      notes.remove("n1"),
      ai.generate("d1", file, "concise"),
      ai.job("j1"),
      ai.cancel("j1"),
      ai.retry("j1"),
      ai.drafts("j1"),
      ai.approve("j1", ["x"]),
    ];
    await Promise.allSettled(all);
    expect(calls.map((c) => `${c.method} ${c.path}`)).toEqual([
      "GET /auth/me",
      "POST /auth/login",
      "POST /auth/signup",
      "POST /auth/logout",
      "POST /auth/verify-email",
      "POST /auth/verify-email/resend",
      "POST /auth/password-reset",
      "POST /auth/password-reset/confirm",
      "GET /decks",
      "GET /decks/d%2F1",
      "POST /decks",
      "PATCH /decks/d1",
      "DELETE /decks/d1",
      "GET /decks/d1/stats",
      "GET /decks/d1/review?limit=100",
      "GET /cards/c1",
      "POST /cards/c1/review",
      "GET /cards?deck_id=d1",
      "POST /cards/bulk",
      "POST /decks/d1/notes",
      "GET /notes/n1",
      "PATCH /notes/n1",
      "DELETE /notes/n1",
      "POST /ai/generate",
      "GET /ai/jobs/j1",
      "POST /ai/jobs/j1/cancel",
      "POST /ai/jobs/j1/retry",
      "GET /ai/jobs/j1/drafts",
      "POST /ai/jobs/j1/approve",
    ]);
    expect(calls[16]?.body).toEqual({ rating: 3, idempotency_key: "k1" });
    expect(calls[23]?.body).toMatchObject({ deck_id: "d1", thoroughness: "concise" });
    expect(ai.eventsPath("j 1")).toBe("/ai/jobs/j%201/events");
  });
});
