// SPDX-License-Identifier: AGPL-3.0-only
// Response and request shapes, mirroring apps/api/src/flashcards/api/schemas.py.

export type Role = "admin" | "member";

export interface User {
  id: string;
  email: string;
  role: Role;
  email_verified: boolean;
}

export interface Deck {
  id: string;
  name: string;
  parent_id: string | null;
  created_at: string;
  /** False for a library deck: an admin's deck the caller may study but not change. */
  owned: boolean;
}

export interface DeckStats {
  total: number;
  new: number;
  learning: number;
  review: number;
  mature: number;
  suspended: number;
  due: number;
  proficiency: number;
}

export type NoteType = "basic" | "cloze" | "custom" | "image_occlusion";

/** A custom note type's card template: text with {{Field}} placeholders. */
export interface CardTemplate {
  ordinal: number;
  name: string;
  front: string;
  back: string;
}

/** A rectangle normalized to the image (0..1); its id is the card ordinal it hides. */
export interface Mask {
  id: number;
  left: number;
  top: number;
  width: number;
  height: number;
}

export interface Occlusion {
  image_id: string;
  masks: Mask[];
}

export type Fields = Record<string, string>;

export interface Card {
  id: string;
  deck_id: string;
  note_id: string;
  ordinal: number;
  suspended: boolean;
  flag: number;
  buried_until: string | null;
}

export interface CardDetail extends Card {
  note_type: NoteType;
  fields: Fields;
  tags: string[];
  note_type_id?: string | null;
  template?: CardTemplate | null;
  occlusion?: Occlusion | null;
  /** False for a library card: it can be studied but not edited or AI-graded. */
  owned: boolean;
}

export interface Note {
  id: string;
  deck_id: string;
  note_type: NoteType;
  fields: Fields;
  tags: string[];
  created_at: string;
  cards: Card[];
  note_type_id?: string | null;
  occlusion?: Occlusion | null;
  /** False for a library note: it can be read but not changed. */
  owned: boolean;
}

export type Rating = 1 | 2 | 3 | 4;

export interface CardSchedule {
  card_id: string;
  state: "learning" | "review" | "relearning";
  due_at: string;
  stability: number | null;
  difficulty: number | null;
}

export const CARD_STATES = ["new", "learning", "review", "suspended", "buried"] as const;
export type CardState = (typeof CARD_STATES)[number];

export const SORT_KEYS = ["created", "due", "proficiency", "deck"] as const;
export type SortKey = (typeof SORT_KEYS)[number];

export interface BrowserRow {
  card_id: string;
  note_id: string;
  deck_id: string;
  deck_name: string;
  ordinal: number;
  note_type: NoteType;
  preview: string;
  tags: string[];
  flag: number;
  state: CardState;
  due_at: string | null;
  proficiency: number;
  created_at: string;
}

export interface BrowserPage {
  total: number;
  cards: BrowserRow[];
}

export interface BrowserQuery {
  deck_id?: string;
  state?: CardState[];
  tag?: string;
  flag?: number;
  q?: string;
  sort?: SortKey;
  order?: "asc" | "desc";
  limit?: number;
  offset?: number;
}

export type BulkAction =
  "move" | "delete" | "add_tags" | "remove_tags" | "suspend" | "unsuspend" | "bury" | "unbury" | "flag";

export interface BulkRequest {
  card_ids: string[];
  action: BulkAction;
  deck_id?: string;
  tags?: string[];
  flag?: number;
}

export interface BulkResult {
  cards: number;
  notes: number;
}

export type Thoroughness = "concise" | "standard" | "thorough";

export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";

export interface Job {
  id: string;
  status: JobStatus;
  progress: number;
  total_units: number;
  completed_units: number;
  cancel_requested: boolean;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface Draft {
  id: string;
  note_type: NoteType;
  fields: Fields;
  source_page: number;
}

export const TERMINAL_JOB_STATUSES: readonly JobStatus[] = ["succeeded", "failed", "cancelled"];

// --- goals and plans (F-PLAN-1, F-PLAN-2) ---

export interface Goal {
  id: string;
  name: string;
  deck_ids: string[];
  exam_date: string;
  target_retention: number;
  created_at: string;
}

export interface GoalInput {
  name: string;
  deck_ids: string[];
  exam_date: string;
  /** The desired-grade slider, 0 to 1; the server maps it onto a retention target. */
  desired_grade: number;
}

export interface StudyPlan {
  days_until_exam: number;
  total_cards: number;
  new_cards: number;
  scheduled_cards: number;
  new_per_day: number;
  reviews_per_day: number;
  readiness: number;
  on_track: boolean;
}

// --- sharing (F-SHARE-1..3) ---

export interface ShareLink {
  id: string;
  deck_id: string;
  allow_clone: boolean;
  created_at: string;
  revoked_at: string | null;
  /** When the link stops working; null when it never expires. */
  expires_at: string | null;
}

/** How long a new link works, in days; null for a link that never expires. */
export type ShareExpiryDays = 1 | 7 | 30 | 90 | null;

/** A new link; its token is shown once, since only a hash of it is stored. */
export interface CreatedShareLink extends ShareLink {
  token: string;
}

export interface SharedNote {
  deck_index: number;
  note_type: NoteType;
  fields: Fields;
}

export interface SharedDeck {
  name: string;
  decks: { name: string; depth: number }[];
  deck_count: number;
  notes: SharedNote[];
  note_count: number;
  allow_clone: boolean;
}

// --- import (F-IMP-1) ---

export interface ImportReport {
  decks_created: number;
  notes_imported: number;
  notes_skipped: number;
}

// --- admin (F-ADMIN-1, F-ADMIN-2, F-ADMIN-5) ---

export type InviteStatus = "active" | "redeemed" | "revoked" | "expired";

export interface Invite {
  id: string;
  role: Role;
  email: string | null;
  status: InviteStatus;
  created_at: string;
  expires_at: string;
}

export interface CreatedInvite {
  id: string;
  token: string;
  role: Role;
  email: string | null;
  expires_at: string;
}

export interface AdminUser {
  id: string;
  email: string;
  role: Role;
  email_verified: boolean;
  suspended: boolean;
  created_at: string;
  /** Whether two-factor sign-in is on; null when the answer did not look it up. */
  two_factor?: boolean | null;
}

export interface AuditEvent {
  id: string;
  action: string;
  created_at: string;
  actor_id: string | null;
  target_id: string | null;
  detail: Record<string, string>;
}

// --- custom note types and media (F-CARD-3, F-CARD-4, F-CARD-5) ---

export interface NoteTypeDef {
  id: string;
  name: string;
  fields: string[];
  templates: CardTemplate[];
  created_at: string;
}

/** A template to save; an ordinal keeps an existing template and its cards. */
export interface TemplateInput {
  ordinal?: number;
  name: string;
  front: string;
  back: string;
}

export interface NoteTypeInput {
  name: string;
  fields: string[];
  templates: TemplateInput[];
}

export interface MediaAsset {
  id: string;
  content_type: string;
}
