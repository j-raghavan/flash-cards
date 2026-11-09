// SPDX-License-Identifier: AGPL-3.0-only
// A deck summary (F-AI-6), asked for on the deck page. It is AI output: it is
// labelled, rendered through CardContent, and never blocks the rest of the page.

import { useEffect, useRef, useState } from "react";

import { aiErrorMessage, isAbort, summarizeDeck, unlockThreshold, type DeckSummary, type Unlock } from "../api/aiStudy";
import { CardContent, ErrorText } from "../components/ui";
import { AiLabel, UnlockNotice } from "./shared";

export function DeckSummaryPanel({ deckId }: { deckId: string }) {
  const [summary, setSummary] = useState<DeckSummary | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [locked, setLocked] = useState<Unlock | null>(null);
  const controller = useRef<AbortController | null>(null);

  // Leaving the page drops the answer instead of waiting for it.
  useEffect(
    () => () => {
      controller.current?.abort();
    },
    [],
  );

  const summarize = (): void => {
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    setBusy(true);
    setError(null);
    setLocked(null);
    summarizeDeck(deckId, current.signal).then(
      (found) => {
        if (current.signal.aborted) {
          return;
        }
        setSummary(found);
        setBusy(false);
      },
      (failure: unknown) => {
        if (isAbort(failure)) {
          return;
        }
        setBusy(false);
        const unlock = unlockThreshold(failure);
        if (unlock === null) {
          setError(aiErrorMessage(failure));
        } else {
          setLocked(unlock);
        }
      },
    );
  };

  return (
    <section className="panel" aria-labelledby="deck-summary-heading">
      <h2 id="deck-summary-heading">Deck summary</h2>
      {summary === null ? (
        <p className="muted">A short overview of what this deck covers, written by the AI from its cards.</p>
      ) : (
        <div className="stack">
          <AiLabel />
          <CardContent aiOutput markdown={summary.summary} />
          {summary.key_points.length > 0 ? (
            <>
              <h3>Key points</h3>
              <ul>
                {summary.key_points.map((point, index) => (
                  // Model text may repeat, so the position is the key.
                  <li key={index}>
                    <CardContent aiOutput markdown={point} className="inline" />
                  </li>
                ))}
              </ul>
            </>
          ) : null}
        </div>
      )}
      {locked === null ? null : <UnlockNotice feature="Deck summaries" unlock={locked} deckId={deckId} />}
      <ErrorText message={error} />
      <div className="row">
        <button
          type="button"
          className="button"
          aria-disabled={busy}
          onClick={() => {
            if (!busy) {
              summarize();
            }
          }}
        >
          {summary === null ? "Summarize this deck" : "Summarize again"}
        </button>
        <span className="muted" role="status">
          {busy ? "Writing the summary…" : summary === null ? "" : "Summary ready."}
        </span>
      </div>
    </section>
  );
}
