// SPDX-License-Identifier: AGPL-3.0-only
// Selective .apkg import with a dry run, and deck export (ADR 0015). The extra
// report fields are optional: a server without them imports the whole package
// and ignores the dry-run flag, which the import screen detects.

import { ApiError, apiUrl, errorMessage, NetworkError, request } from "./client";
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

export function exportUrl(deckId: string, format: ExportFormat): string {
  return apiUrl(`/decks/${encodeURIComponent(deckId)}/export?format=${format}`);
}

export interface ExportedDeck {
  blob: Blob;
  filename: string;
  /** Custom and image occlusion notes left out (they travel in JSON only). */
  omittedNotes: number;
}

/**
 * Fetch a deck export. Fetched rather than linked, so a refusal (too large, a
 * lapsed session) shows as a message instead of downloading the error body.
 */
export async function exportDeck(deckId: string, format: ExportFormat): Promise<ExportedDeck> {
  let response: Response;
  try {
    response = await fetch(exportUrl(deckId, format), {
      credentials: "same-origin",
      headers: { "X-Requested-With": "fetch" },
    });
  } catch {
    throw new NetworkError();
  }
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    throw new ApiError(response.status, errorMessage(response.status, body));
  }
  const disposition = /filename="([^"]+)"/.exec(response.headers.get("Content-Disposition") ?? "");
  return {
    blob: await response.blob(),
    filename: disposition?.[1] ?? `deck.${format}`,
    omittedNotes: Number(response.headers.get("X-Omitted-Notes") ?? "0") || 0,
  };
}

/** Hand a file to the browser's downloads. */
export function saveFile(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
