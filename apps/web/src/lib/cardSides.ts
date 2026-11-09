// SPDX-License-Identifier: AGPL-3.0-only
import type { CardTemplate, Fields, NoteType } from "../api/types";
import { clozeSide } from "./cloze";

export interface CardSides {
  front: string;
  back: string;
}

const PLACEHOLDER = /\{\{(.*?)\}\}/gs;
const FRONT_SIDE = "FrontSide";

/** A custom template with its {{Field}} placeholders filled in (the server's rules: ADR 0013). */
function fillTemplate(template: string, fields: Fields, front: string): string {
  return template.replace(PLACEHOLDER, (_match, name: string) => (name === FRONT_SIDE ? front : (fields[name] ?? "")));
}

function withExtra(back: string, extra: string): string {
  return extra === "" ? back : `${back}\n\n---\n\n${extra}`;
}

/**
 * The Markdown shown before and after the answer is revealed. For an image
 * occlusion card it is the header and the back extra; the image is drawn apart.
 */
export function cardSides(
  noteType: NoteType,
  fields: Fields,
  ordinal: number,
  template?: CardTemplate | null,
): CardSides {
  switch (noteType) {
    case "basic":
      return { front: fields.front ?? "", back: fields.back ?? "" };
    case "cloze": {
      const text = fields.text ?? "";
      return {
        front: clozeSide(text, ordinal, false),
        back: withExtra(clozeSide(text, ordinal, true), fields.extra ?? ""),
      };
    }
    case "custom": {
      const front = template == null ? "" : fillTemplate(template.front, fields, "");
      return { front, back: template == null ? "" : fillTemplate(template.back, fields, front) };
    }
    case "image_occlusion":
      return { front: fields.header ?? "", back: fields.back_extra ?? "" };
  }
}
