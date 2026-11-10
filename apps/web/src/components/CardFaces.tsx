// SPDX-License-Identifier: AGPL-3.0-only
// A card as it is studied: the front, any image occlusion, then the back below a
// divider once revealed. Shared by the study session and the browser's card view.

import type { CardDetail } from "../api/types";
import { cardSides } from "../lib/cardSides";
import { OcclusionImage } from "./OcclusionImage";
import { CardContent } from "./ui";

export function CardFaces({ detail, revealed }: { detail: CardDetail; revealed: boolean }) {
  const sides = cardSides(detail.note_type, detail.fields, detail.ordinal, detail.template);
  const occlusion = detail.occlusion ?? null;
  return (
    <>
      <CardContent markdown={sides.front} className="front" />
      {occlusion === null ? null : (
        <OcclusionImage occlusion={occlusion} ordinal={detail.ordinal} revealed={revealed} />
      )}
      {revealed ? (
        <>
          <hr />
          <CardContent markdown={sides.back} className="back" />
        </>
      ) : null}
    </>
  );
}
