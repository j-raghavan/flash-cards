// SPDX-License-Identifier: AGPL-3.0-only
// Pages that change a deck (add a card, generate, share, mock exam) are the
// owner's. A library deck (ADR 0024) reached by a typed or old link shows a short
// read-only notice instead of a form whose every action the server refuses.

import type { ReactNode } from "react";
import { Link, useParams } from "react-router";

import { decks } from "../api/endpoints";
import { isOwned } from "../lib/ownership";
import { useDocumentTitle } from "../lib/useDocumentTitle";
import { useResource } from "../lib/useResource";
import { Loading } from "./ui";

export function LibraryNotice({ deckId, title }: { deckId: string; title: string }) {
  useDocumentTitle(title);
  return (
    <section>
      <nav aria-label="Breadcrumb" className="breadcrumb">
        <Link to={`/decks/${deckId}`}>Back to deck</Link>
      </nav>
      <h1>{title}</h1>
      <p>This deck is in the library; only its owner can change it.</p>
      <p className="row">
        <Link className="button primary" to={`/decks/${deckId}/study`}>
          Study it
        </Link>
        <Link className="button" to={`/decks/${deckId}`}>
          Back to the deck
        </Link>
      </p>
    </section>
  );
}

/**
 * Render the page only for the user's own deck. A deck that cannot be loaded
 * is left to the page, which reports the failure as it always has.
 */
export function OwnerOnly({ title, children }: { title: string; children: ReactNode }) {
  const { deckId = "" } = useParams();
  const deck = useResource(() => decks.get(deckId), `owner-only:${deckId}`);
  if (deck.data === undefined && deck.error === null) {
    return <Loading />;
  }
  if (deck.data !== undefined && !isOwned(deck.data)) {
    return <LibraryNotice deckId={deckId} title={title} />;
  }
  return <>{children}</>;
}
