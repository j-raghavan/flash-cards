// SPDX-License-Identifier: AGPL-3.0-only
// Pieces shared by the AI study screens.

import { Link } from "react-router";

import type { Unlock } from "../api/aiStudy";

/** Every piece of AI output carries this label (PRD section 7). */
export function AiLabel({ children = "AI-generated. Check it against your own material." }: { children?: string }) {
  return <p className="ai-label">{children}</p>;
}

/** A feature that unlocks at a card count, and how far the deck is from it. */
export function UnlockNotice({ feature, unlock, deckId }: { feature: string; unlock: Unlock; deckId: string }) {
  const missing = Math.max(0, unlock.required - unlock.have);
  return (
    <div className="empty" role="status">
      <p>
        {feature} unlock once a deck has {unlock.required} cards. This deck has {unlock.have}
        {missing > 0 ? `, so add ${String(missing)} more` : ""}.
      </p>
      <p>
        <Link to={`/decks/${deckId}/notes/new`}>Add a card</Link> or{" "}
        <Link to={`/decks/${deckId}/generate`}>generate cards from a file</Link>.
      </p>
    </div>
  );
}
