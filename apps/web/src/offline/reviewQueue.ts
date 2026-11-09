// SPDX-License-Identifier: AGPL-3.0-only
// Offline review (F-STUDY-5). An answer that cannot reach the server is kept in
// IndexedDB with its idempotency key and replayed later. The server ignores a
// key it has already seen, so replaying an answer that did arrive is harmless.

import { openDB, type DBSchema, type IDBPDatabase } from "idb";

import { ApiError, NetworkError } from "../api/client";
import { cards } from "../api/endpoints";
import type { Rating } from "../api/types";

export interface QueuedReview {
  idempotency_key: string;
  user_id: string;
  card_id: string;
  deck_id: string;
  rating: Rating;
  queued_at: number;
}

interface OfflineDb extends DBSchema {
  reviews: {
    key: string;
    value: QueuedReview;
    indexes: { by_user: string };
  };
}

const DB_NAME = "flashcards-offline";
let dbPromise: Promise<IDBPDatabase<OfflineDb>> | null = null;

function db(): Promise<IDBPDatabase<OfflineDb>> {
  dbPromise ??= openDB<OfflineDb>(DB_NAME, 1, {
    upgrade(database) {
      const store = database.createObjectStore("reviews", { keyPath: "idempotency_key" });
      store.createIndex("by_user", "user_id");
    },
  });
  return dbPromise;
}

/** Test hook: forget the open connection so a fresh database can be used. */
export async function resetForTests(): Promise<void> {
  if (dbPromise !== null) {
    (await dbPromise).close();
  }
  dbPromise = null;
}

type Listener = () => void;
const listeners = new Set<Listener>();

/** Notified whenever the queue changes. Returns an unsubscribe function. */
export function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function notify(): void {
  for (const listener of listeners) {
    listener();
  }
}

export async function enqueue(review: QueuedReview): Promise<void> {
  await (await db()).put("reviews", review);
  notify();
}

/** The user's queued answers, oldest first. */
export async function pending(userId: string): Promise<QueuedReview[]> {
  const all = await (await db()).getAllFromIndex("reviews", "by_user", userId);
  return all.sort((a, b) => a.queued_at - b.queued_at);
}

async function remove(key: string): Promise<void> {
  await (await db()).delete("reviews", key);
}

function isTransient(error: unknown): boolean {
  return (
    error instanceof NetworkError ||
    (error instanceof ApiError && (error.status >= 500 || error.status === 429 || error.status === 408))
  );
}

export type SubmitOutcome = "sent" | "queued";

/** Send an answer now, or queue it when the server cannot be reached. */
export async function submitReview(review: Omit<QueuedReview, "queued_at">): Promise<SubmitOutcome> {
  const queued: QueuedReview = { ...review, queued_at: Date.now() };
  if (!navigator.onLine) {
    await enqueue(queued);
    return "queued";
  }
  try {
    await cards.review(review.card_id, review.rating, review.idempotency_key);
    return "sent";
  } catch (error) {
    if (isTransient(error)) {
      await enqueue(queued);
      return "queued";
    }
    throw error;
  }
}

export interface FlushResult {
  sent: number;
  dropped: number;
  remaining: number;
}

let flushing: Promise<FlushResult> | null = null;

/**
 * Replay the user's queued answers in order. Stops at the first transient
 * failure (still offline, server trouble) or when the session has ended, and
 * drops an answer the server rejects for good, such as one for a deleted card.
 */
export function flush(userId: string): Promise<FlushResult> {
  flushing ??= doFlush(userId).finally(() => {
    flushing = null;
  });
  return flushing;
}

async function doFlush(userId: string): Promise<FlushResult> {
  const queue = await pending(userId);
  let sent = 0;
  let dropped = 0;
  for (const review of queue) {
    try {
      await cards.review(review.card_id, review.rating, review.idempotency_key);
      sent += 1;
    } catch (error) {
      if (isTransient(error) || (error instanceof ApiError && error.status === 401)) {
        break;
      }
      dropped += 1;
    }
    await remove(review.idempotency_key);
  }
  if (sent + dropped > 0) {
    notify();
  }
  return { sent, dropped, remaining: queue.length - sent - dropped };
}
