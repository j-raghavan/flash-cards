// SPDX-License-Identifier: AGPL-3.0-only
import { describe, expect, it } from "vitest";

import { mockApi } from "../test/helpers";
import { admin, ai, auth, browserParams, cards, decks, goals, imports, media, notes, noteTypes, shares } from "./endpoints";
import { plans, progress, reviews } from "./studyProgress";

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

  it("map goals, sharing, import, admin, note types and media to the API routes", async () => {
    const calls = mockApi({});
    const file = new File(["x"], "a.apkg");
    const occlusion = { image_id: "m1", masks: [{ id: 1, left: 0, top: 0, width: 0.5, height: 0.5 }] };
    const types = { name: "T", fields: ["A"], templates: [{ name: "C", front: "{{A}}", back: "{{A}}" }] };
    await Promise.allSettled([
      notes.create("d1", "image_occlusion", { header: "" }, [], { occlusion }),
      notes.update("n1", { header: "h" }, [], occlusion),
      goals.list(),
      goals.get("g1"),
      goals.create({ name: "Exam", deck_ids: ["d1"], exam_date: "2026-12-01T00:00:00Z", desired_grade: 0.5 }),
      goals.update("g1", { name: "Exam 2" }),
      goals.remove("g1"),
      goals.plan("g1"),
      shares.list("d1"),
      shares.create("d1", true),
      shares.setAllowClone("s1", false),
      shares.revoke("s1"),
      shares.preview("t/1"),
      shares.clone("t1"),
      imports.apkg(file),
      admin.invites(),
      admin.createInvite(null, "member", 7),
      admin.revokeInvite("i1"),
      admin.users(),
      admin.suspend("u1"),
      admin.unsuspend("u1"),
      admin.unlock("u1"),
      admin.setRole("u1", "admin"),
      admin.removeUser("u1"),
      admin.audit(50, null),
      admin.audit(50, "e9"),
      noteTypes.list(),
      noteTypes.get("nt1"),
      noteTypes.create(types),
      noteTypes.update("nt1", { ...types, renamed: { B: "A" } }),
      noteTypes.remove("nt1"),
      media.upload(file),
      progress.today(),
      progress.settings(),
      progress.updateSettings({ daily_goal: 30 }),
      progress.stats(null, 90, 30),
      progress.stats("d1", 7, 14),
      reviews.undo("r1"),
      plans.report("g1"),
      plans.accept("g1"),
    ]);
    expect(calls.map((c) => `${c.method} ${c.path}`)).toEqual([
      "POST /decks/d1/notes",
      "PATCH /notes/n1",
      "GET /goals",
      "GET /goals/g1",
      "POST /goals",
      "PATCH /goals/g1",
      "DELETE /goals/g1",
      "GET /goals/g1/plan",
      "GET /decks/d1/shares",
      "POST /decks/d1/shares",
      "PATCH /shares/s1",
      "POST /shares/s1/revoke",
      "GET /shared/t%2F1",
      "POST /shared/t1/clone",
      "POST /import/apkg",
      "GET /admin/invites",
      "POST /admin/invites",
      "POST /admin/invites/i1/revoke",
      "GET /admin/users",
      "POST /admin/users/u1/suspend",
      "POST /admin/users/u1/unsuspend",
      "POST /admin/users/u1/unlock",
      "PATCH /admin/users/u1",
      "DELETE /admin/users/u1",
      "GET /admin/audit?limit=50",
      "GET /admin/audit?limit=50&before=e9",
      "GET /note-types",
      "GET /note-types/nt1",
      "POST /note-types",
      "PATCH /note-types/nt1",
      "DELETE /note-types/nt1",
      "POST /media",
      "GET /progress/today",
      "GET /progress/settings",
      "PATCH /progress/settings",
      "GET /progress/stats?days=90&forecast_days=30",
      "GET /progress/stats?days=7&forecast_days=14&deck_id=d1",
      "POST /reviews/r1/undo",
      "GET /goals/g1/plan",
      "POST /goals/g1/plan/accept",
    ]);
    expect(calls[0]?.body).toEqual({ note_type: "image_occlusion", fields: { header: "" }, tags: [], occlusion });
    expect(calls[1]?.body).toEqual({ fields: { header: "h" }, tags: [], occlusion });
    expect(calls[9]?.body).toEqual({ allow_clone: true });
    expect(calls[16]?.body).toEqual({ email: null, role: "member", ttl_days: 7 });
    expect(calls[22]?.body).toEqual({ role: "admin" });
    expect(calls[29]?.body).toEqual({ ...types, renamed: { B: "A" } });
  });
});
