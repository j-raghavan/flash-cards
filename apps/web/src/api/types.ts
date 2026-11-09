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
}

export interface Note {
  id: string;
  deck_id: string;
  note_type: NoteType;
  fields: Fields;
  tags: string[];
  created_at: string;
  cards: Card[];
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
  | "move"
  | "delete"
  | "add_tags"
  | "remove_tags"
  | "suspend"
  | "unsuspend"
  | "bury"
  | "unbury"
  | "flag";

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
