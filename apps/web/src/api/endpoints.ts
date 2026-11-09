// SPDX-License-Identifier: AGPL-3.0-only
// One typed function per API route the client uses.

import { request } from "./client";
import type {
  BrowserPage,
  BrowserQuery,
  BulkRequest,
  BulkResult,
  Card,
  CardDetail,
  CardSchedule,
  Deck,
  DeckStats,
  Draft,
  Fields,
  Job,
  Note,
  NoteType,
  Rating,
  Thoroughness,
  User,
} from "./types";

const id = encodeURIComponent;

// --- auth ---

export const auth = {
  me: () => request<User>("/auth/me"),
  login: (email: string, password: string) =>
    request<User>("/auth/login", { method: "POST", json: { email, password } }),
  signup: (token: string, email: string, password: string) =>
    request<User>("/auth/signup", { method: "POST", json: { token, email, password } }),
  logout: () => request<undefined>("/auth/logout", { method: "POST" }),
  verifyEmail: (token: string) => request<undefined>("/auth/verify-email", { method: "POST", json: { token } }),
  resendVerification: () => request<undefined>("/auth/verify-email/resend", { method: "POST" }),
  requestPasswordReset: (email: string) =>
    request<undefined>("/auth/password-reset", { method: "POST", json: { email } }),
  confirmPasswordReset: (token: string, password: string) =>
    request<undefined>("/auth/password-reset/confirm", {
      method: "POST",
      json: { token, password },
    }),
};

// --- decks ---

export const decks = {
  list: () => request<Deck[]>("/decks"),
  get: (deckId: string) => request<Deck>(`/decks/${id(deckId)}`),
  create: (name: string, parentId: string | null) =>
    request<Deck>("/decks", { method: "POST", json: { name, parent_id: parentId } }),
  rename: (deckId: string, name: string) => request<Deck>(`/decks/${id(deckId)}`, { method: "PATCH", json: { name } }),
  remove: (deckId: string) => request<undefined>(`/decks/${id(deckId)}`, { method: "DELETE" }),
  stats: (deckId: string) => request<DeckStats>(`/decks/${id(deckId)}/stats`),
  dueQueue: (deckId: string, limit = 100) => request<Card[]>(`/decks/${id(deckId)}/review?limit=${limit}`),
};

// --- cards and notes ---

export const cards = {
  get: (cardId: string) => request<CardDetail>(`/cards/${id(cardId)}`),
  review: (cardId: string, rating: Rating, idempotencyKey: string) =>
    request<CardSchedule>(`/cards/${id(cardId)}/review`, {
      method: "POST",
      json: { rating, idempotency_key: idempotencyKey },
    }),
  browse: (query: BrowserQuery) => request<BrowserPage>(`/cards?${browserParams(query)}`),
  bulk: (body: BulkRequest) => request<BulkResult>("/cards/bulk", { method: "POST", json: body }),
};

export function browserParams(query: BrowserQuery): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query) as [string, string | number | string[] | undefined][]) {
    if (Array.isArray(value)) {
      for (const item of value) {
        params.append(key, item);
      }
    } else if (value !== undefined && value !== "") {
      params.set(key, String(value));
    }
  }
  return params.toString();
}

export const notes = {
  create: (deckId: string, noteType: NoteType, fields: Fields, tags: string[]) =>
    request<Card[]>(`/decks/${id(deckId)}/notes`, {
      method: "POST",
      json: { note_type: noteType, fields, tags },
    }),
  get: (noteId: string) => request<Note>(`/notes/${id(noteId)}`),
  update: (noteId: string, fields: Fields, tags: string[]) =>
    request<Note>(`/notes/${id(noteId)}`, { method: "PATCH", json: { fields, tags } }),
  remove: (noteId: string) => request<undefined>(`/notes/${id(noteId)}`, { method: "DELETE" }),
};

// --- AI generation ---

export const ai = {
  generate: (deckId: string, file: File, thoroughness: Thoroughness) => {
    const form = new FormData();
    form.append("file", file);
    form.append("deck_id", deckId);
    form.append("thoroughness", thoroughness);
    return request<Job>("/ai/generate", { method: "POST", form });
  },
  /** The caller's generation jobs for a deck, newest first (the API caps limit at 50). */
  jobs: (deckId: string, limit = 5) =>
    request<Job[]>(`/ai/jobs?${new URLSearchParams({ deck_id: deckId, limit: String(limit) }).toString()}`),
  job: (jobId: string) => request<Job>(`/ai/jobs/${id(jobId)}`),
  cancel: (jobId: string) => request<Job>(`/ai/jobs/${id(jobId)}/cancel`, { method: "POST" }),
  retry: (jobId: string) => request<Job>(`/ai/jobs/${id(jobId)}/retry`, { method: "POST" }),
  drafts: (jobId: string) => request<Draft[]>(`/ai/jobs/${id(jobId)}/drafts`),
  approve: (jobId: string, draftIds: string[]) =>
    request<Card[]>(`/ai/jobs/${id(jobId)}/approve`, {
      method: "POST",
      json: { draft_ids: draftIds },
    }),
  eventsPath: (jobId: string) => `/ai/jobs/${id(jobId)}/events`,
};
