// SPDX-License-Identifier: AGPL-3.0-only
// Read a deck's cards without studying or editing them: one card at a time, front
// and back, as it is studied. Previous and Next (or the arrow keys) step through
// the deck; the position is kept in the address (?card=N), so a reload or a shared
// link opens the same card. Works for library decks too (ADR 0024); the Edit link
// appears only on the caller's own cards. Nothing here changes a schedule.

import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";

import { cards, decks } from "../api/endpoints";
import type { Card } from "../api/types";
import { CardFaces } from "../components/CardFaces";
import { ErrorText, Loading } from "../components/ui";
import { deckPath } from "../lib/deckTree";
import { isOwned } from "../lib/ownership";
import { useDocumentTitle } from "../lib/useDocumentTitle";
import { useResource } from "../lib/useResource";

function ReaderCard({ card, revealed }: { card: Card; revealed: boolean }) {
  const detail = useResource(() => cards.get(card.id), `card:${card.id}`);
  if (detail.data === undefined) {
    return detail.error === null ? <Loading label="Loading card…" /> : <ErrorText message={detail.error} />;
  }
  return (
    <>
      <article className="study-card card-view" aria-label="Card content">
        <CardFaces detail={detail.data} revealed={revealed} />
      </article>
      {isOwned(detail.data) ? (
        <p className="row">
          <Link className="button" to={`/notes/${card.note_id}/edit`}>
            Edit this card
          </Link>
        </p>
      ) : null}
    </>
  );
}

/** The 0-based position the address asks for, or the first card when it asks for none or one out of range. */
export function readerIndex(asked: string | null, total: number): number {
  const position = Number(asked);
  return Number.isInteger(position) && position >= 1 && position <= total ? position - 1 : 0;
}

export function DeckReaderPage() {
  const { deckId = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const all = useResource(() => decks.list(), `decks:${deckId}`);
  const list = useResource(() => decks.cards(deckId), `deck-cards:${deckId}`);
  const [revealed, setRevealed] = useState(true);
  const previousButton = useRef<HTMLButtonElement>(null);
  const nextButton = useRef<HTMLButtonElement>(null);

  const deck = all.data?.find((d) => d.id === deckId);
  useDocumentTitle(deck === undefined ? "Read cards" : `Read: ${deck.name}`);

  const total = list.data?.length ?? 0;
  const index = readerIndex(params.get("card"), total);
  const hasPrevious = index > 0;
  const hasNext = index + 1 < total;

  // A button that becomes disabled at an end of the deck would drop focus, so
  // focus moves to the other one. The arrow keys step the same way.
  const step = useCallback(
    (to: number): void => {
      setParams({ card: String(to + 1) }, { replace: true });
      if (to === 0) {
        nextButton.current?.focus();
      } else if (to === total - 1) {
        previousButton.current?.focus();
      }
    },
    [setParams, total],
  );

  useEffect(() => {
    const onKey = (event: KeyboardEvent): void => {
      if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) {
        return;
      }
      if (event.key === "ArrowLeft" && hasPrevious) {
        event.preventDefault();
        step(index - 1);
      } else if (event.key === "ArrowRight" && hasNext) {
        event.preventDefault();
        step(index + 1);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
    };
  }, [index, hasPrevious, hasNext, step]);

  if (all.error !== null || list.error !== null) {
    return <ErrorText message={all.error ?? list.error} />;
  }
  if (all.data === undefined || list.data === undefined) {
    return <Loading />;
  }
  if (deck === undefined) {
    return (
      <section>
        <h1>Deck not found</h1>
        <p>
          <Link to="/">Back to your decks</Link>
        </p>
      </section>
    );
  }

  const card = list.data[index];
  return (
    <section>
      <nav aria-label="Breadcrumb" className="breadcrumb">
        <Link to="/">Decks</Link>
        {[...deckPath(all.data, deckId), deck].map((ancestor) => (
          <span key={ancestor.id}>
            {" / "}
            <Link to={`/decks/${ancestor.id}`}>{ancestor.name}</Link>
          </span>
        ))}
      </nav>
      <h1>Read: {deck.name}</h1>
      {card === undefined ? (
        <>
          <p className="empty">This deck has no cards to read yet.</p>
          <p>
            <Link to={`/decks/${deckId}`}>Back to the deck</Link>
          </p>
        </>
      ) : (
        <>
          <p className="muted" role="status">
            Card {index + 1} of {total}
          </p>
          <ReaderCard key={card.id} card={card} revealed={revealed} />
          <div className="row actions">
            <button
              type="button"
              className="button"
              ref={previousButton}
              aria-keyshortcuts="ArrowLeft"
              disabled={!hasPrevious}
              onClick={() => {
                step(index - 1);
              }}
            >
              Previous card
            </button>
            <button
              type="button"
              className="button primary"
              ref={nextButton}
              aria-keyshortcuts="ArrowRight"
              disabled={!hasNext}
              onClick={() => {
                step(index + 1);
              }}
            >
              Next card
            </button>
            <button
              type="button"
              className="button"
              aria-pressed={!revealed}
              onClick={() => {
                setRevealed((shown) => !shown);
              }}
            >
              Hide answers
            </button>
            <Link className="button" to={`/decks/${deckId}/study`}>
              Study this deck
            </Link>
          </div>
        </>
      )}
    </section>
  );
}
