// SPDX-License-Identifier: AGPL-3.0-only
// One typed function per API route the client uses.

import { request } from "./client";
import type { ReviewResult } from "./studyProgress";
import type { SecondFactorRequired } from "./twoFactor";
import type {
  AdminUser,
  AuditEvent,
  BrowserPage,
  BrowserQuery,
  BulkRequest,
  BulkResult,
  Card,
  CardDetail,
  CreatedInvite,
  CreatedShareLink,
  Deck,
  DeckStats,
  Draft,
  Fields,
  Goal,
  GoalInput,
  Invite,
  Job,
  MediaAsset,
  Note,
  NoteType,
  NoteTypeDef,
  NoteTypeInput,
  Occlusion,
  Rating,
  Role,
  SharedDeck,
  ShareExpiryDays,
  ShareLink,
  StudyPlan,
  Thoroughness,
  User,
} from "./types";

const id = encodeURIComponent;

// --- auth ---

export const auth = {
  me: () => request<User>("/auth/me"),
  /** The user, or (202) a pending sign-in that a second factor must finish. */
  login: (email: string, password: string) =>
    request<User | SecondFactorRequired>("/auth/login", { method: "POST", json: { email, password } }),
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
    request<ReviewResult>(`/cards/${id(cardId)}/review`, {
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

/** What a custom or image occlusion note carries besides its fields. */
export interface NoteExtras {
  note_type_id?: string;
  occlusion?: Occlusion;
}

export const notes = {
  create: (deckId: string, noteType: NoteType, fields: Fields, tags: string[], extras: NoteExtras = {}) =>
    request<Card[]>(`/decks/${id(deckId)}/notes`, {
      method: "POST",
      json: { note_type: noteType, fields, tags, ...extras },
    }),
  get: (noteId: string) => request<Note>(`/notes/${id(noteId)}`),
  update: (noteId: string, fields: Fields, tags: string[], occlusion?: Occlusion) =>
    request<Note>(`/notes/${id(noteId)}`, {
      method: "PATCH",
      json: occlusion === undefined ? { fields, tags } : { fields, tags, occlusion },
    }),
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
  /** From a YouTube link's transcript (F-AI-2); 422 when the video has none to fetch. */
  generateFromYoutube: (deckId: string, url: string, thoroughness: Thoroughness) =>
    request<Job>("/ai/generate/youtube", { method: "POST", json: { deck_id: deckId, url, thoroughness } }),
  generateFromTranscript: (deckId: string, transcript: string, thoroughness: Thoroughness) =>
    request<Job>("/ai/generate/transcript", {
      method: "POST",
      json: { deck_id: deckId, transcript, thoroughness },
    }),
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

// --- goals and plans ---

export const goals = {
  list: () => request<Goal[]>("/goals"),
  get: (goalId: string) => request<Goal>(`/goals/${id(goalId)}`),
  create: (input: GoalInput) => request<Goal>("/goals", { method: "POST", json: input }),
  update: (goalId: string, input: Partial<GoalInput>) =>
    request<Goal>(`/goals/${id(goalId)}`, { method: "PATCH", json: input }),
  remove: (goalId: string) => request<undefined>(`/goals/${id(goalId)}`, { method: "DELETE" }),
  plan: (goalId: string) => request<StudyPlan>(`/goals/${id(goalId)}/plan`),
};

// --- sharing ---

export const shares = {
  list: (deckId: string) => request<ShareLink[]>(`/decks/${id(deckId)}/shares`),
  create: (deckId: string, allowClone: boolean, expiresInDays: ShareExpiryDays) =>
    request<CreatedShareLink>(`/decks/${id(deckId)}/shares`, {
      method: "POST",
      json: { allow_clone: allowClone, expires_in_days: expiresInDays },
    }),
  setAllowClone: (shareId: string, allowClone: boolean) =>
    request<ShareLink>(`/shares/${id(shareId)}`, { method: "PATCH", json: { allow_clone: allowClone } }),
  revoke: (shareId: string) => request<ShareLink>(`/shares/${id(shareId)}/revoke`, { method: "POST" }),
  preview: (token: string) => request<SharedDeck>(`/shared/${id(token)}`),
  clone: (token: string) => request<Deck>(`/shared/${id(token)}/clone`, { method: "POST" }),
};

// --- admin ---

export const admin = {
  invites: () => request<Invite[]>("/admin/invites"),
  createInvite: (email: string | null, role: Role, ttlDays: number) =>
    request<CreatedInvite>("/admin/invites", { method: "POST", json: { email, role, ttl_days: ttlDays } }),
  revokeInvite: (inviteId: string) => request<undefined>(`/admin/invites/${id(inviteId)}/revoke`, { method: "POST" }),
  users: () => request<AdminUser[]>("/admin/users"),
  createUser: (email: string, password: string, role: Role) =>
    request<AdminUser>("/admin/users", { method: "POST", json: { email, password, role } }),
  setPassword: (userId: string, password: string) =>
    request<AdminUser>(`/admin/users/${id(userId)}/password`, { method: "POST", json: { password } }),
  suspend: (userId: string) => request<AdminUser>(`/admin/users/${id(userId)}/suspend`, { method: "POST" }),
  unsuspend: (userId: string) => request<AdminUser>(`/admin/users/${id(userId)}/unsuspend`, { method: "POST" }),
  unlock: (userId: string) => request<AdminUser>(`/admin/users/${id(userId)}/unlock`, { method: "POST" }),
  setRole: (userId: string, role: Role) =>
    request<AdminUser>(`/admin/users/${id(userId)}`, { method: "PATCH", json: { role } }),
  removeUser: (userId: string) => request<undefined>(`/admin/users/${id(userId)}`, { method: "DELETE" }),
  audit: (limit: number, before: string | null) => {
    const params = new URLSearchParams({ limit: String(limit) });
    if (before !== null) {
      params.set("before", before);
    }
    return request<AuditEvent[]>(`/admin/audit?${params.toString()}`);
  },
};

// --- custom note types and media ---

export const noteTypes = {
  list: () => request<NoteTypeDef[]>("/note-types"),
  get: (noteTypeId: string) => request<NoteTypeDef>(`/note-types/${id(noteTypeId)}`),
  create: (input: NoteTypeInput) => request<NoteTypeDef>("/note-types", { method: "POST", json: input }),
  update: (noteTypeId: string, input: NoteTypeInput & { renamed: Record<string, string> }) =>
    request<NoteTypeDef>(`/note-types/${id(noteTypeId)}`, { method: "PATCH", json: input }),
  remove: (noteTypeId: string) => request<undefined>(`/note-types/${id(noteTypeId)}`, { method: "DELETE" }),
};

export const media = {
  upload: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<MediaAsset>("/media", { method: "POST", form });
  },
};
