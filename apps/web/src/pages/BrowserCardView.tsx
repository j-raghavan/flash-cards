// SPDX-License-Identifier: AGPL-3.0-only
// The card browser's read-only view of one card, rendered as it is studied:
// front, then back below a divider. Previous and Next (or the arrow keys) step
// through the cards of the current result page; Escape closes the view and
// returns focus to the row that opened it. Editing is a link away, and only
// for the caller's own cards.

import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router";

import { cards } from "../api/endpoints";
import type { BrowserRow } from "../api/types";
import { CardFaces } from "../components/CardFaces";
import { Dialog, ErrorText, Loading } from "../components/ui";
import { isOwned } from "../lib/ownership";
import { useResource } from "../lib/useResource";

function CardBody({ row, revealed }: { row: BrowserRow; revealed: boolean }) {
  const detail = useResource(() => cards.get(row.card_id), `card:${row.card_id}`);
  return (
    <>
      <ErrorText message={detail.error} />
      {detail.data === undefined ? (
        detail.error === null ? (
          <Loading label="Loading card…" />
        ) : null
      ) : (
        <>
          <article className="study-card card-view" aria-label="Card content">
            <CardFaces detail={detail.data} revealed={revealed} />
          </article>
          {isOwned(detail.data) ? (
            <p className="row">
              <Link className="button" to={`/notes/${row.note_id}/edit`}>
                Edit
              </Link>
            </p>
          ) : null}
        </>
      )}
    </>
  );
}

export function BrowserCardView({
  rows,
  index,
  onMove,
  onClose,
}: {
  rows: BrowserRow[];
  index: number;
  onMove: (index: number) => void;
  onClose: () => void;
}) {
  const [revealed, setRevealed] = useState(true);
  const row = rows[index];
  const hasPrevious = index > 0;
  const hasNext = index + 1 < rows.length;
  const previousButton = useRef<HTMLButtonElement>(null);
  const nextButton = useRef<HTMLButtonElement>(null);
  // A button that becomes disabled at an end of the page would drop focus out of
  // the dialog, so focus moves to the other one. The arrow keys step the same way.
  const step = useCallback(
    (to: number): void => {
      onMove(to);
      if (to === 0) {
        nextButton.current?.focus();
      } else if (to === rows.length - 1) {
        previousButton.current?.focus();
      }
    },
    [onMove, rows.length],
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
      } else if (event.key === "Escape" && !event.defaultPrevented) {
        // The dialog closes on Escape while focus is inside it; this also covers
        // focus that ended up outside it (on the page body).
        event.preventDefault();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
    };
  }, [index, hasPrevious, hasNext, step, onClose]);

  if (row === undefined) {
    return null;
  }

  return (
    <Dialog title={`Card ${String(index + 1)} of ${String(rows.length)}`} onClose={onClose}>
      <dl className="card-view-meta">
        <div>
          <dt>Deck</dt>
          <dd>{row.deck_name}</dd>
        </div>
        <div>
          <dt>State</dt>
          <dd>{row.state}</dd>
        </div>
        <div>
          <dt>Tags</dt>
          <dd>{row.tags.length === 0 ? "None" : row.tags.join(" ")}</dd>
        </div>
      </dl>
      <CardBody key={row.card_id} row={row} revealed={revealed} />
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
          className="button"
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
          Hide answer
        </button>
      </div>
    </Dialog>
  );
}
