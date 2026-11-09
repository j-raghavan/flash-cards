// SPDX-License-Identifier: AGPL-3.0-only
// A study session (F-STUDY-1, F-STUDY-3): show the question, reveal the answer,
// rate it. Space reveals, 1 to 4 rate, U undoes the last answer and E edits the
// card. Every answer carries its own idempotency key and is queued on the
// device when the server is unreachable (F-STUDY-5), so the session never waits
// on the network.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router";

import { ApiError, describeError } from "../api/client";
import { cards, decks, notes } from "../api/endpoints";
import { reviews, type DailyGoal } from "../api/studyProgress";
import type { Card, CardDetail, Rating } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { OcclusionImage } from "../components/OcclusionImage";
import { CardContent, Dialog, ErrorText, Loading } from "../components/ui";
import { cardSides } from "../lib/cardSides";
import { newIdempotencyKey } from "../lib/text";
import { useDocumentTitle } from "../lib/useDocumentTitle";
import { useResource } from "../lib/useResource";
import { pending, submitReview } from "../offline/reviewQueue";
import { NoteEditor } from "./NoteEditorPage";

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

/** The latest answer, kept so it can be taken back (ADR 0014). */
interface LastAnswer {
  key: string;
  card: Card;
  round: number;
  index: number;
  /** Set once the server has recorded the answer; a queued answer cannot be undone. */
  reviewId: string | null;
}

/** Today's count against the daily goal, and a quiet word when it is reached. */
function GoalProgress({ goal }: { goal: DailyGoal }) {
  return goal.just_completed ? (
    <p className="celebration" role="status">
      Daily goal reached: {goal.reviews} cards today. Well done.
    </p>
  ) : (
    <p className="muted">
      Today: {goal.reviews} of {goal.daily_goal}
      {goal.goal_met ? " (goal met)" : ""}
    </p>
  );
}

function EditCardDialog({
  noteId,
  onSaved,
  onClose,
}: {
  noteId: string;
  onSaved: () => Promise<void>;
  onClose: () => void;
}) {
  const note = useResource(() => notes.get(noteId), noteId);
  return (
    <Dialog title="Edit card" onClose={onClose}>
      <ErrorText message={note.error} />
      {note.data === undefined ? (
        note.error === null ? (
          <Loading />
        ) : null
      ) : (
        <NoteEditor
          deckId={note.data.deck_id}
          initial={note.data}
          withPreview={false}
          onSaved={() => {
            void onSaved();
          }}
        />
      )}
    </Dialog>
  );
}

export function StudyPage() {
  const { deckId = "" } = useParams();
  const { user } = useAuth();
  const userId = user?.id ?? "";
  useDocumentTitle("Study");

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
  const [lastAnswer, setLastAnswer] = useState<LastAnswer | null>(null);
  const [undoing, setUndoing] = useState(false);
  const [goal, setGoal] = useState<DailyGoal | null>(null);
  const [editing, setEditing] = useState(false);

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
      // No answer while an undo is on its way: the undo moves the session back.
      if (card === undefined || undoing) {
        return;
      }
      setSubmitError(null);
      answered.current.add(card.id);
      const key = newIdempotencyKey();
      setLastAnswer({ key, card, round, index, reviewId: null });
      const sending = submitReview({
        idempotency_key: key,
        user_id: userId,
        card_id: card.id,
        deck_id: deckId,
        rating,
      }).then(
        (sent) => {
          if (sent.outcome === "queued") {
            setQueuedCount((n) => n + 1);
            return;
          }
          const { review_id: reviewId, daily_goal: dailyGoal } = sent.result;
          if (reviewId !== undefined) {
            setLastAnswer((last) => (last?.key === key ? { ...last, reviewId } : last));
          }
          if (dailyGoal !== undefined) {
            setGoal(dailyGoal);
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
    [card, userId, deckId, advance, round, index, undoing],
  );

  // The session's position when an undo answers, which may differ from when it was sent.
  const where = useRef({ round, index, queue });
  useEffect(() => {
    where.current = { round, index, queue };
  });

  // The answer an undo would take back: one the server recorded, while the
  // session is showing cards and no other undo is on its way.
  const undoTarget = useMemo(
    () =>
      lastAnswer !== null && lastAnswer.reviewId !== null && queue !== null && !undoing
        ? { last: lastAnswer, reviewId: lastAnswer.reviewId, queue }
        : null,
    [lastAnswer, queue, undoing],
  );

  // Take the last answer back and show its card again, as a question.
  const undo = useCallback(
    (target: { last: LastAnswer; reviewId: string; queue: Card[] }) => {
      const { last, reviewId } = target;
      setUndoing(true);
      setSubmitError(null);
      const request = reviews.undo(reviewId).then(
        (result) => {
          const now = where.current;
          setGoal(result.daily_goal);
          answered.current.delete(last.card.id);
          setReviewed((n) => n - 1);
          setRevealedAt(null);
          setLastAnswer(null);
          if (last.round === now.round) {
            setIndex(last.index);
          } else {
            // A new round began after the answer: put the card first in it.
            const rest = (now.queue ?? target.queue).slice(now.index).filter((c) => c.id !== last.card.id);
            setQueue([last.card, ...rest]);
            setIndex(0);
          }
        },
        (failure: unknown) => {
          setSubmitError(describeError(failure));
          // The server refused (too late, already undone): nothing left to undo.
          // A lost connection or a server error leaves the undo to try again.
          if (failure instanceof ApiError && failure.status < 500) {
            setLastAnswer(null);
          }
        },
      );
      // A new round waits for the undo too, so its queue includes the card.
      inFlight.current.add(request);
      void request.finally(() => {
        inFlight.current.delete(request);
        setUndoing(false);
      });
    },
    [],
  );

  // After an edit, show the card as it is now; an edit can remove it (a cloze
  // deletion taken out), which then shows as a card that will not load.
  const refreshCard = useCallback(
    async (cardId: string) => {
      details.current.delete(cardId);
      try {
        const fresh = await fetchDetail(cardId);
        setShown({ position, detail: fresh, error: null });
      } catch (failure) {
        setShown({ position, detail: null, error: describeError(failure) });
      }
      setEditing(false);
    },
    [position, fetchDetail],
  );

  useEffect(() => {
    const onKey = (event: KeyboardEvent): void => {
      if (editing || isTyping(event.target) || event.altKey) {
        return;
      }
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "z") {
        if (undoTarget !== null) {
          event.preventDefault();
          undo(undoTarget);
        }
        return;
      }
      if (event.metaKey || event.ctrlKey) {
        return;
      }
      if (event.key.toLowerCase() === "u" && undoTarget !== null) {
        event.preventDefault();
        undo(undoTarget);
        return;
      }
      if (event.key.toLowerCase() === "e" && detail !== null) {
        event.preventDefault();
        setEditing(true);
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
  }, [revealed, detail, rate, position, editing, undoTarget, undo]);

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

  const undoButton =
    undoTarget === null ? null : (
      <button
        type="button"
        className="button"
        aria-keyshortcuts="U Control+Z Meta+Z"
        onClick={() => {
          undo(undoTarget);
        }}
      >
        Undo last answer <kbd>U</kbd>
      </button>
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
        {goal === null ? null : <GoalProgress goal={goal} />}
        <p role="status">
          {reviewed === 0
            ? "Nothing is due in this deck right now."
            : `You reviewed ${reviewed} card${reviewed === 1 ? "" : "s"}.`}
          {queuedCount > 0 ? ` ${queuedCount} will sync when you are back online.` : ""}
        </p>
        <ErrorText message={submitError} />
        <div className="row">
          <Link className="button primary" to={`/decks/${deckId}`}>
            Back to deck
          </Link>
          {undoButton}
        </div>
      </section>
    );
  }

  const sides = detail === null ? null : cardSides(detail.note_type, detail.fields, detail.ordinal, detail.template);
  const occlusion = detail?.occlusion ?? null;

  return (
    <section className="study">
      {header}
      <div className="row study-meta">
        <p className="muted">
          Card {index + 1} of {queue.length}
        </p>
        {goal === null ? null : <GoalProgress goal={goal} />}
      </div>
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
              disabled={undoing}
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
      <div className="row actions">
        {undoButton}
        {detail === null ? null : (
          <button
            type="button"
            className="button"
            aria-keyshortcuts="E"
            onClick={() => {
              setEditing(true);
            }}
          >
            Edit card <kbd>E</kbd>
          </button>
        )}
      </div>
      {editing ? (
        <EditCardDialog
          noteId={card.note_id}
          onSaved={() => refreshCard(card.id)}
          onClose={() => {
            setEditing(false);
          }}
        />
      ) : null}
    </section>
  );
}
