// SPDX-License-Identifier: AGPL-3.0-only
import { describe, expect, it, vi } from "vitest";

import { mockApi } from "../test/helpers";
import { STUDY_DATA_CACHE, clearStudyDataCache } from "./apiCache";
import { enqueue, flush, pending, submitReview, subscribe, type QueuedReview } from "./reviewQueue";

const review = (key: string, user = "u1", at = 1): QueuedReview => ({
  idempotency_key: key,
  user_id: user,
  card_id: `card-${key}`,
  deck_id: "d1",
  rating: 3,
  queued_at: at,
});

function setOnline(value: boolean): void {
  Object.defineProperty(navigator, "onLine", { configurable: true, get: () => value });
}

describe("submitReview", () => {
  const answer = { idempotency_key: "k1", user_id: "u1", card_id: "c1", deck_id: "d1", rating: 4 as const };

  it("sends the answer with its idempotency key when online", async () => {
    const calls = mockApi({ "POST /cards/c1/review": { body: {} } });
    await expect(submitReview(answer)).resolves.toBe("sent");
    expect(calls[0]?.body).toEqual({ rating: 4, idempotency_key: "k1" });
    expect(await pending("u1")).toEqual([]);
  });

  it("queues without trying when the browser is offline", async () => {
    const calls = mockApi({});
    setOnline(false);
    await expect(submitReview(answer)).resolves.toBe("queued");
    expect(calls).toEqual([]);
    expect((await pending("u1")).map((r) => r.idempotency_key)).toEqual(["k1"]);
  });

  it("queues when the request fails in transit or the server is struggling", async () => {
    for (const reply of [new TypeError("offline"), { status: 503 }, { status: 429 }, { status: 408 }]) {
      mockApi({ "POST /cards/c1/review": reply });
      await expect(
        submitReview({ ...answer, idempotency_key: `k-${String(reply instanceof Error ? 0 : reply.status)}` }),
      ).resolves.toBe("queued");
    }
    expect(await pending("u1")).toHaveLength(4);
  });

  it("keeps an answer whose session has lapsed, to send after signing in again", async () => {
    mockApi({ "POST /cards/c1/review": { status: 401, body: { detail: "Not authenticated." } } });
    await expect(submitReview(answer)).resolves.toBe("queued");
    expect(await pending("u1")).toHaveLength(1);
  });

  it("reports a permanent rejection instead of queueing it", async () => {
    mockApi({ "POST /cards/c1/review": { status: 404, body: { detail: "Not found." } } });
    await expect(submitReview(answer)).rejects.toThrow("Not found.");
    expect(await pending("u1")).toEqual([]);
  });
});

describe("flush", () => {
  it("replays a user's answers oldest first and leaves other users' alone", async () => {
    await enqueue(review("b", "u1", 2));
    await enqueue(review("a", "u1", 1));
    await enqueue(review("z", "u2", 0));
    const calls = mockApi({ "POST /cards/card-a/review": { body: {} }, "POST /cards/card-b/review": { body: {} } });
    const listener = vi.fn();
    const unsubscribe = subscribe(listener);
    await expect(flush("u1")).resolves.toEqual({ sent: 2, dropped: 0, remaining: 0 });
    expect(calls.map((c) => c.path)).toEqual(["/cards/card-a/review", "/cards/card-b/review"]);
    expect(calls[0]?.body).toEqual({ rating: 3, idempotency_key: "a" });
    expect(listener).toHaveBeenCalled();
    unsubscribe();
    expect(await pending("u1")).toEqual([]);
    expect(await pending("u2")).toHaveLength(1);
  });

  it("drops answers the server rejects for good and stops at a transient failure", async () => {
    await enqueue(review("a", "u1", 1));
    await enqueue(review("b", "u1", 2));
    await enqueue(review("c", "u1", 3));
    mockApi({
      "POST /cards/card-a/review": { status: 404, body: { detail: "gone" } },
      "POST /cards/card-b/review": { status: 502 },
    });
    await expect(flush("u1")).resolves.toEqual({ sent: 0, dropped: 1, remaining: 2 });
    expect((await pending("u1")).map((r) => r.idempotency_key)).toEqual(["b", "c"]);
  });

  it("stops when the session has ended", async () => {
    await enqueue(review("a"));
    mockApi({ "POST /cards/card-a/review": { status: 401, body: { detail: "Not authenticated." } } });
    await expect(flush("u1")).resolves.toEqual({ sent: 0, dropped: 0, remaining: 1 });
  });

  it("shares one run between overlapping calls and notifies no one when nothing changed", async () => {
    mockApi({});
    const listener = vi.fn();
    subscribe(listener);
    const first = flush("u1");
    expect(flush("u1")).toBe(first);
    expect(flush("u2")).not.toBe(first);
    await expect(first).resolves.toEqual({ sent: 0, dropped: 0, remaining: 0 });
    expect(listener).not.toHaveBeenCalled();
  });
});

describe("clearStudyDataCache", () => {
  it("deletes the study data cache", async () => {
    const remove = vi.fn(() => Promise.resolve(true));
    vi.stubGlobal("caches", { delete: remove });
    await clearStudyDataCache();
    expect(remove).toHaveBeenCalledWith(STUDY_DATA_CACHE);
  });

  it("is quiet when Cache Storage is missing or blocked", async () => {
    vi.stubGlobal("caches", undefined);
    await expect(clearStudyDataCache()).resolves.toBeUndefined();
    vi.stubGlobal("caches", { delete: () => Promise.reject(new Error("SecurityError")) });
    await expect(clearStudyDataCache()).resolves.toBeUndefined();
  });
});
