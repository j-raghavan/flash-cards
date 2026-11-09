// SPDX-License-Identifier: AGPL-3.0-only
// AI-evaluation study mode (F-STUDY-2): the student types an answer, the AI
// grades it against the card and explains the gap, and the study session then
// shows the answer with a suggested rating that the student confirms or
// changes. The explanation is model output, rendered only through CardContent.

import { useEffect, useRef, useState, type KeyboardEvent, type SubmitEvent } from "react";

import {
  VERDICT_LABELS,
  aiErrorMessage,
  evaluateAnswer,
  isAbort,
  percent,
  suggestedRating,
  type Evaluation,
} from "../api/aiStudy";
import type { Rating } from "../api/types";
import { CardContent, ErrorText } from "../components/ui";
import { AiLabel } from "./shared";

const MAX_ANSWER = 20_000;

export const RATING_NAMES: Record<Rating, string> = { 1: "Again", 2: "Hard", 3: "Good", 4: "Easy" };

type Phase = { name: "typing" } | { name: "checking" } | { name: "graded"; evaluation: Evaluation };

export interface TypedAnswerProps {
  cardId: string;
  /** The session is showing the card's answer. */
  revealed: boolean;
  /** The AI graded the answer: show the card's answer and suggest this rating. */
  onEvaluated: (rating: Rating) => void;
  /** Show the answer without asking the AI. */
  onReveal: () => void;
}

export function TypedAnswer({ cardId, revealed, onEvaluated, onReveal }: TypedAnswerProps) {
  const [answer, setAnswer] = useState("");
  const [phase, setPhase] = useState<Phase>({ name: "typing" });
  const [error, setError] = useState<string | null>(null);
  const boxRef = useRef<HTMLTextAreaElement>(null);
  const controller = useRef<AbortController | null>(null);
  const onEvaluatedRef = useRef(onEvaluated);
  useEffect(() => {
    onEvaluatedRef.current = onEvaluated;
  });

  // Each card starts with the cursor in the answer box; a card that moves on
  // drops its evaluation rather than waiting for it.
  useEffect(() => {
    boxRef.current?.focus();
    return () => {
      controller.current?.abort();
    };
  }, []);

  const check = (): void => {
    const text = answer.trim();
    if (text === "" || phase.name !== "typing") {
      return;
    }
    const current = new AbortController();
    controller.current = current;
    setError(null);
    setPhase({ name: "checking" });
    // The Check button is disabled while checking; focus waits in the box.
    boxRef.current?.focus();
    evaluateAnswer(cardId, text, current.signal).then(
      (evaluation) => {
        // The body was already read when the card moved on: drop it.
        if (current.signal.aborted) {
          return;
        }
        setPhase({ name: "graded", evaluation });
        onEvaluatedRef.current(suggestedRating(evaluation));
      },
      (failure: unknown) => {
        if (isAbort(failure)) {
          return;
        }
        // The box stayed on screen (read-only) and keeps its focus.
        setPhase({ name: "typing" });
        setError(aiErrorMessage(failure));
      },
    );
  };

  const onSubmit = (event: SubmitEvent): void => {
    event.preventDefault();
    check();
  };

  // Enter checks; Shift+Enter starts a new line; Escape leaves the box so the
  // session's own shortcuts (undo, edit) work again.
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>): void => {
    if (event.key === "Escape") {
      event.currentTarget.blur();
      return;
    }
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      check();
    }
  };

  const status =
    phase.name === "checking"
      ? "The AI is checking your answer…"
      : phase.name === "graded"
        ? `Checked: ${VERDICT_LABELS[phase.evaluation.verdict]}. ` +
          `Suggested rating: ${RATING_NAMES[suggestedRating(phase.evaluation)]}.`
        : "";

  return (
    <div className="typed-answer-panel">
      {phase.name !== "graded" && !revealed ? (
        <form className="stack" onSubmit={onSubmit} aria-label="Type your answer">
          <label className="field">
            <span>Your answer</span>
            <textarea
              ref={boxRef}
              rows={3}
              maxLength={MAX_ANSWER}
              value={answer}
              readOnly={phase.name === "checking"}
              aria-describedby="typed-answer-hint"
              onChange={(event) => {
                setAnswer(event.target.value);
              }}
              onKeyDown={onKeyDown}
            />
          </label>
          <p id="typed-answer-hint" className="muted">
            Enter checks your answer; Shift+Enter starts a new line; Esc leaves the box.
          </p>
          <ErrorText message={error} />
          <div className="row">
            <button
              type="submit"
              className="button primary"
              disabled={answer.trim() === "" || phase.name === "checking"}
            >
              {phase.name === "checking" ? "Checking…" : "Check answer"}
            </button>
            <button type="button" className="button" onClick={onReveal}>
              {phase.name === "checking" ? "Show answer now" : "Show answer without checking"}
            </button>
          </div>
        </form>
      ) : null}
      {phase.name === "graded" || (revealed && answer.trim() !== "") ? (
        <div className="stack">
          <p className="muted">Your answer:</p>
          <p className="typed-answer">{answer.trim()}</p>
        </div>
      ) : null}
      {phase.name === "graded" ? (
        <div className="evaluation">
          <AiLabel>AI evaluation. You choose the final rating.</AiLabel>
          <p>
            <strong>{VERDICT_LABELS[phase.evaluation.verdict]}</strong>{" "}
            <span className="muted">({percent(phase.evaluation.score)})</span>
          </p>
          <CardContent markdown={phase.evaluation.explanation} />
          <p className="muted">
            Suggested rating: {RATING_NAMES[suggestedRating(phase.evaluation)]}. Press Enter to accept it, or 1 to 4
            to choose another.
          </p>
        </div>
      ) : null}
      <p className="visually-hidden" role="status">
        {status}
      </p>
    </div>
  );
}
