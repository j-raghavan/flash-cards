// SPDX-License-Identifier: AGPL-3.0-only
import type { Fields, NoteType } from "../api/types";
import { clozeSide } from "./cloze";

export interface CardSides {
  front: string;
  back: string;
}

/** The Markdown shown before and after the answer is revealed. */
export function cardSides(noteType: NoteType, fields: Fields, ordinal: number): CardSides {
  if (noteType === "basic") {
    return { front: fields.front ?? "", back: fields.back ?? "" };
  }
  const text = fields.text ?? "";
  const extra = fields.extra ?? "";
  const back = clozeSide(text, ordinal, true);
  return {
    front: clozeSide(text, ordinal, false),
    back: extra === "" ? back : `${back}\n\n---\n\n${extra}`,
  };
}
