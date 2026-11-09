// SPDX-License-Identifier: AGPL-3.0-only
// Selective .apkg import with a dry run, and deck export (ADR 0015). The extra
// report fields are optional: a server without them imports the whole package
// and ignores the dry-run flag, which the import screen detects.

import { apiUrl, request } from "./client";
import type { ImportReport } from "./types";

export type UnsupportedKind =
  | "unknown_note_type"
  | "unmappable_note"
  | "invalid_note"
  | "duplicate_note"
  | "dropped_fields"
  | "extra_cards"
  | "filtered_deck"
  | "media"
  | "note_without_cards"
  | "skipped_reviews"
  | "history_truncated";

export interface UnsupportedConstruct {
  kind: UnsupportedKind;
  count: number;
  examples: string[];
}

export interface FullImportReport extends ImportReport {
  notes_not_selected?: number;
  cards_scheduled?: number;
  reviews_imported?: number;
  dry_run?: boolean;
  unsupported?: UnsupportedConstruct[];
}

export interface ImportOptions {
  dryRun: boolean;
  /** Anki deck names (with their sub-decks) to import; empty imports every deck. */
  decks: string[];
  /** Tags to import (with descendant tags); empty imports every note. */
  tags: string[];
}

export const MAX_SELECTION_ITEMS = 50;
export const MAX_SELECTION_LENGTH = 200;

export function importApkg(file: File, options: ImportOptions): Promise<FullImportReport> {
  const form = new FormData();
  form.append("file", file);
  form.append("dry_run", options.dryRun ? "true" : "false");
  for (const deck of options.decks) {
    form.append("deck", deck);
  }
  for (const tag of options.tags) {
    form.append("tag", tag);
  }
  return request<FullImportReport>("/import/apkg", { method: "POST", form });
}

export type ExportFormat = "apkg" | "csv" | "json";

/** A same-origin download link; the session cookie goes with it. */
export function exportUrl(deckId: string, format: ExportFormat): string {
  return apiUrl(`/decks/${encodeURIComponent(deckId)}/export?format=${format}`);
}
