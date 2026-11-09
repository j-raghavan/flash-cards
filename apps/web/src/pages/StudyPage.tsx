// SPDX-License-Identifier: AGPL-3.0-only
// A study session (F-STUDY-1, F-STUDY-3): show the question, reveal the answer,
// rate it. Space reveals, 1 to 4 rate. Every answer carries its own
// idempotency key and is queued on the device when the server is unreachable
// (F-STUDY-5), so the session never waits on the network.

import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router";

import { describeError } from "../api/client";
import { cards, decks } from "../api/endpoints";
import type { Card, CardDetail, Rating } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { OcclusionImage } from "../components/OcclusionImage";
import { CardContent, ErrorText, Loading } from "../components/ui";
import { cardSides } from "../lib/cardSides";
import { newIdempotencyKey } from "../lib/text";
import { pending, submitReview } from "../offline/reviewQueue";

const RATINGS: { rating: Rating; label: string }[] = [
  { rating: 1, label: "Again" },
  { rating: 2, label: "Hard" },
  { rating: 3, label: "Good" },
  { rating: 4, label: "Easy" },
];

function isTyping(target: EventTarget | null): boolean {
  return (
    target instanceof HTMLElement &&
    (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName))
  );
}

export function StudyPage() {
  const { deckId = "" } = useParams();
  const { user } = useAuth();
  const userId = user?.id ?? "";

  const [queue, setQueue] = useState<Card[] | null>(null);
  const [index, setIndex] = useState(0);
  // The card on screen, keyed by its position so a new position starts blank.
  const [shown, setShown] = useState<{ position: string; detail: CardDetail | null; error: string | null }>({
    position: "",
    detail: null,
    error: null,
  });
  const [revealedAt, setRevealedAt] = useState<string | null>(null);
  const [reviewed, setReviewed] = useState(0);
  const [queuedCount, setQueuedCount] = useState(0);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [round, setRound] = useState(0);

  const details = useRef(new Map<string, Promise<CardDetail>>());
  const inFlight = useRef(new Set<Promise<unknown>>());
  const answered = useRef(new Set<string>());

  const fetchDetail = useCallback((cardId: string): Promise<CardDetail> => {
    let found = details.current.get(cardId);
    if (found === undefined) {
      found = cards.get(cardId);
      details.current.set(cardId, found);
      found.catch(() => {
        details.current.delete(cardId);
      });
    }
    return found;
  }, []);

  // Load (or, after a round, reload) the due queue, leaving out answers that are
  // still waiting on this device to be sent.
  useEffect(() => {
    let current = true;
    const load = async (): Promise<void> => {
      await Promise.allSettled([...inFlight.current]);
      const [due, waiting] = await Promise.all([decks.dueQueue(deckId), pending(userId)]);
      const skip = new Set(waiting.map((review) => review.card_id));
      if (current) {
        details.current.clear();
        setQueue(due.filter((card) => !skip.has(card.id)));
        setIndex(0);
      }
    };
    load().catch((failure: unknown) => {
      if (!current) {
        return;
      }
      if (round > 0) {
        setQueue([]); // a later round that cannot load just ends the session
      } else {
        setLoadError(describeError(failure));
      }
    });
    return () => {
      current = false;
    };
  }, [deckId, userId, round]);

  const card = queue?.[index];
  const nextCard = queue?.[index + 1];
  const position = `${String(round)}:${String(index)}`;
  const detail = shown.position === position ? shown.detail : null;
  const cardError = shown.position === position ? shown.error : null;
  const revealed = revealedAt === position;

  useEffect(() => {
    if (card === undefined) {
      return;
    }
    let current = true;
    fetchDetail(card.id).then(
      (found) => {
        if (current) {
          setShown({ position, detail: found, error: null });
        }
      },
      (failure: unknown) => {
        if (current) {
          setShown({ position, detail: null, error: describeError(failure) });
        }
      },
    );
    if (nextCard !== undefined) {
      fetchDetail(nextCard.id).catch(() => undefined);
    }
    return () => {
      current = false;
    };
  }, [card, nextCard, position, fetchDetail]);

  const advance = useCallback(() => {
    if (queue !== null && index + 1 >= queue.length && answered.current.size > 0) {
      // The end of this round: look again, since cards rated Again come back soon.
      answered.current.clear();
      setQueue(null);
      setRound((n) => n + 1);
      return;
    }
    setIndex((i) => i + 1);
  }, [queue, index]);

  const rate = useCallback(
    (rating: Rating) => {
      if (card === undefined) {
        return;
      }
      setSubmitError(null);
      answered.current.add(card.id);
      const sending = submitReview({
        idempotency_key: newIdempotencyKey(),
        user_id: userId,
        card_id: card.id,
        deck_id: deckId,
        rating,
      }).then(
        (outcome) => {
          if (outcome === "queued") {
            setQueuedCount((n) => n + 1);
          }
        },
        (failure: unknown) => {
          setSubmitError(describeError(failure));
        },
      );
      inFlight.current.add(sending);
      void sending.finally(() => inFlight.current.delete(sending));
      setReviewed((n) => n + 1);
      advance();
    },
    [card, userId, deckId, advance],
  );

  useEffect(() => {
    const onKey = (event: KeyboardEvent): void => {
      if (isTyping(event.target) || event.metaKey || event.ctrlKey || event.altKey) {
        return;
      }
      // Space and Enter on a link or button keep their own meaning.
      const onControl = event.target instanceof HTMLElement && ["A", "BUTTON"].includes(event.target.tagName);
      if ((event.key === " " || event.key === "Enter") && !onControl && !revealed && detail !== null) {
        event.preventDefault();
        setRevealedAt(position);
        return;
      }
      const found = RATINGS.find((r) => String(r.rating) === event.key);
      if (found !== undefined && revealed) {
        event.preventDefault();
        rate(found.rating);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
    };
  }, [revealed, detail, rate, position]);

  // Keep keyboard and screen-reader focus with the session: the card when it
  // appears, the rating buttons once the answer shows.
  const cardRef = useRef<HTMLElement>(null);
  const ratingsRef = useRef<HTMLDivElement>(null);
  const loaded = detail !== null;
  useEffect(() => {
    if (loaded) {
      (revealed ? ratingsRef.current : cardRef.current)?.focus();
    }
  }, [loaded, revealed, position]);

  const header = (
    <nav aria-label="Breadcrumb" className="breadcrumb">
      <Link to={`/decks/${deckId}`}>Back to deck</Link>
    </nav>
  );

  if (loadError !== null) {
    return (
      <section>
        {header}
        <ErrorText message={loadError} />
      </section>
    );
  }
  if (queue === null) {
    return (
      <section>
        {header}
        <Loading label="Loading your cards…" />
      </section>
    );
  }
  if (card === undefined) {
    return (
      <section className="study-done">
        {header}
        <h1>All caught up</h1>
        <p role="status">
          {reviewed === 0
            ? "Nothing is due in this deck right now."
            : `You reviewed ${reviewed} card${reviewed === 1 ? "" : "s"}.`}
          {queuedCount > 0 ? ` ${queuedCount} will sync when you are back online.` : ""}
        </p>
        <ErrorText message={submitError} />
        <p>
          <Link className="button primary" to={`/decks/${deckId}`}>
            Back to deck
          </Link>
        </p>
      </section>
    );
  }

  const sides = detail === null ? null : cardSides(detail.note_type, detail.fields, detail.ordinal, detail.template);
  const occlusion = detail?.occlusion ?? null;

  return (
    <section className="study">
      {header}
      <p className="muted">
        Card {index + 1} of {queue.length}
      </p>
      <ErrorText message={submitError} />
      {cardError !== null ? (
        <div>
          <ErrorText message={cardError} />
          <button type="button" className="button" onClick={advance}>
            Skip this card
          </button>
        </div>
      ) : null}
      {sides === null ? (
        cardError === null ? (
          <Loading label="Loading card…" />
        ) : null
      ) : (
        <article
          className="study-card"
          aria-label={`Card ${String(index + 1)} of ${String(queue.length)}`}
          tabIndex={-1}
          ref={cardRef}
        >
          <CardContent markdown={sides.front} className="front" />
          {occlusion === null ? null : (
            <OcclusionImage occlusion={occlusion} ordinal={card.ordinal} revealed={revealed} />
          )}
          {revealed ? (
            <>
              <hr />
              <CardContent markdown={sides.back} className="back" />
            </>
          ) : null}
        </article>
      )}
      {sides === null ? null : revealed ? (
        <div className="ratings" role="group" aria-label="How well did you know it?" tabIndex={-1} ref={ratingsRef}>
          {RATINGS.map(({ rating, label }) => (
            <button
              key={rating}
              type="button"
              className={rating === 3 ? "button primary" : "button"}
              aria-keyshortcuts={String(rating)}
              onClick={() => {
                rate(rating);
              }}
            >
              {label} <kbd>{rating}</kbd>
            </button>
          ))}
        </div>
      ) : (
        <button
          type="button"
          className="button primary reveal"
          aria-keyshortcuts="Space"
          onClick={() => {
            setRevealedAt(position);
          }}
        >
          Show answer <kbd>Space</kbd>
        </button>
      )}
    </section>
  );
}
