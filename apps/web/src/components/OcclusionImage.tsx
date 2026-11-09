// SPDX-License-Identifier: AGPL-3.0-only
// An image occlusion card (F-CARD-3, ADR 0013), in Anki's "hide all, guess one"
// mode: the front hides every mask and marks the card's own; the back reveals
// the card's own masks and keeps the others hidden.

import { apiUrl } from "../api/client";
import type { Occlusion } from "../api/types";

export function OcclusionImage({
  occlusion,
  ordinal,
  revealed,
}: {
  occlusion: Occlusion;
  ordinal: number;
  revealed: boolean;
}) {
  return (
    <div className="occlusion">
      <img src={apiUrl(`/media/${encodeURIComponent(occlusion.image_id)}`)} alt="Image with hidden parts" />
      {occlusion.masks.map((mask, index) => {
        const own = mask.id === ordinal;
        if (own && revealed) {
          return null;
        }
        return (
          <span
            key={`${String(mask.id)}-${String(index)}`}
            className={own ? "mask mask-own" : "mask"}
            aria-hidden="true"
            // Set through the DOM style API, which the CSP allows.
            style={{
              left: `${String(mask.left * 100)}%`,
              top: `${String(mask.top * 100)}%`,
              width: `${String(mask.width * 100)}%`,
              height: `${String(mask.height * 100)}%`,
            }}
          />
        );
      })}
    </div>
  );
}
