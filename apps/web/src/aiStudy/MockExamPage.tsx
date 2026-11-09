// SPDX-License-Identifier: AGPL-3.0-only
// A mock exam (F-AI-7): the AI composes questions from the deck, the student
// answers them, optionally against a calm timer, and the AI grades the answers
// with a breakdown by topic. Questions and topics are model output and are
// rendered only through CardContent.

import { useCallback, useEffect, useRef, useState, type SubmitEvent } from "react";
import { Link, useParams } from "react-router";

import {
  MAX_EXAM_QUESTIONS,
  aiErrorMessage,
  mockExams,
  percent,
  unlockThreshold,
  type ExamResult,
  type MockExam,
  type Unlock,
} from "../api/aiStudy";
import { ApiError } from "../api/client";
import { CardContent, ErrorText } from "../components/ui";
import { useDocumentTitle } from "../lib/useDocumentTitle";
import { AiLabel, UnlockNotice } from "./shared";

const MINUTE = 60_000;

/** How long is left, in words that do not count every second. */
export function timeLeftText(ms: number): string {
  if (ms <= 0) {
    return "Time is up";
  }
  if (ms < MINUTE) {
    return "Less than a minute left";
  }
  const minutes = Math.ceil(ms / MINUTE);
  return `About ${String(minutes)} minute${minutes === 1 ? "" : "s"} left`;
}

/**
 * The time left, updated quietly every few seconds. Screen readers hear it
 * only at five minutes and one minute left, not on every change.
 */
function ExamTimer({ endsAt, onExpire }: { endsAt: number; onExpire: () => void }) {
  const [now, setNow] = useState(() => Date.now());
  const expired = useRef(false);
  const onExpireRef = useRef(onExpire);
  useEffect(() => {
    onExpireRef.current = onExpire;
  });

  useEffect(() => {
    const timer = window.setInterval(() => {
      setNow(Date.now());
    }, 1000);
    return () => {
      window.clearInterval(timer);
    };
  }, []);

  const left = endsAt - now;
  useEffect(() => {
    if (left <= 0 && !expired.current) {
      expired.current = true;
      onExpireRef.current();
    }
  }, [left]);

  const announcement =
    left <= 0
      ? "Time is up."
      : left <= MINUTE
        ? "One minute left."
        : left <= 5 * MINUTE
          ? "Five minutes left."
          : "";
  return (
    <>
      <p className="muted exam-timer" role="timer" aria-label="Time left">
        {timeLeftText(left)}
      </p>
      <p className="visually-hidden" aria-live="polite">
        {announcement}
      </p>
    </>
  );
}

function SetupForm({ deckId, onReady }: { deckId: string; onReady: (exam: MockExam) => void }) {
  const [count, setCount] = useState("10");
  const [timed, setTimed] = useState(false);
  const [minutes, setMinutes] = useState("20");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [locked, setLocked] = useState<Unlock | null>(null);

  const questionCount = Number(count);
  const timeLimit = Number(minutes);
  const valid =
    Number.isInteger(questionCount) &&
    questionCount >= 1 &&
    questionCount <= MAX_EXAM_QUESTIONS &&
    (!timed || (Number.isInteger(timeLimit) && timeLimit >= 1 && timeLimit <= 1440));

  const onSubmit = (event: SubmitEvent): void => {
    event.preventDefault();
    if (busy) {
      return;
    }
    setBusy(true);
    setError(null);
    setLocked(null);
    mockExams.create(deckId, questionCount, timed ? timeLimit * 60 : null).then(
      (exam) => {
        setBusy(false);
        onReady(exam);
      },
      (failure: unknown) => {
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
    <form className="stack" onSubmit={onSubmit} aria-label="Set up a mock exam">
      <p className="muted">
        The AI writes questions from this deck&rsquo;s cards. Answer in your own words; the AI then grades each answer
        and shows how you did by topic.
      </p>
      <label className="field">
        <span>Number of questions (1 to {MAX_EXAM_QUESTIONS})</span>
        <input
          type="number"
          min={1}
          max={MAX_EXAM_QUESTIONS}
          required
          value={count}
          onChange={(event) => {
            setCount(event.target.value);
          }}
        />
      </label>
      <label>
        <input
          type="checkbox"
          checked={timed}
          onChange={(event) => {
            setTimed(event.target.checked);
          }}
        />{" "}
        Time the exam
      </label>
      {timed ? (
        <label className="field">
          <span>Time limit in minutes</span>
          <input
            type="number"
            min={1}
            max={1440}
            required
            value={minutes}
            onChange={(event) => {
              setMinutes(event.target.value);
            }}
          />
        </label>
      ) : null}
      {locked === null ? null : <UnlockNotice feature="Mock exams" unlock={locked} deckId={deckId} />}
      <ErrorText message={error} />
      <div>
        <button type="submit" className="button primary" disabled={!valid} aria-disabled={busy}>
          {busy ? "Composing your exam…" : "Start the exam"}
        </button>
      </div>
      {busy ? (
        <p className="muted" role="status">
          Composing questions. This can take up to a minute.
        </p>
      ) : null}
    </form>
  );
}

function ExamForm({
  exam,
  onGraded,
  onAgain,
}: {
  exam: MockExam;
  onGraded: (exam: MockExam, result: ExamResult, answers: Record<string, string>) => void;
  onAgain: () => void;
}) {
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [closed, setClosed] = useState(false);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const latest = useRef(answers);
  useEffect(() => {
    latest.current = answers;
  });
  // The deadline is rebuilt on this device's clock from the time limit, so a
  // clock that differs from the server's does not shorten the exam.
  const [endsAt] = useState(() =>
    exam.time_limit_seconds === null || exam.deadline === null
      ? null
      : Date.now() + (Date.parse(exam.deadline) - Date.parse(exam.created_at)),
  );

  useEffect(() => {
    headingRef.current?.focus();
  }, []);

  const sending = useRef(false);

  const submit = useCallback(() => {
    // The timer can run out while a submission is already on its way.
    if (sending.current) {
      return;
    }
    sending.current = true;
    setBusy(true);
    setError(null);
    const given = latest.current;
    const payload = exam.questions
      .map((q) => ({ question_id: q.id, answer: (given[q.id] ?? "").trim() }))
      .filter((a) => a.answer !== "");
    mockExams.submit(exam.id, payload).then(
      (result) => {
        onGraded(exam, result, given);
      },
      (failure: unknown) => {
        sending.current = false;
        setBusy(false);
        // 409: already submitted, or past the time limit. Nothing more to send.
        const isClosed = failure instanceof ApiError && failure.status === 409;
        setClosed(isClosed);
        setError(
          isClosed
            ? "This exam can no longer be submitted: it was already submitted or its time ran out."
            : aiErrorMessage(failure),
        );
      },
    );
  }, [exam, onGraded]);

  const unanswered = exam.questions.filter((q) => (answers[q.id] ?? "").trim() === "").length;

  const onSubmit = (event: SubmitEvent): void => {
    event.preventDefault();
    submit();
  };

  return (
    <form className="stack" onSubmit={onSubmit} aria-labelledby="exam-heading">
      <h2 id="exam-heading" tabIndex={-1} ref={headingRef}>
        {exam.questions.length} question{exam.questions.length === 1 ? "" : "s"}
      </h2>
      <AiLabel>AI-generated questions, written from this deck&rsquo;s cards.</AiLabel>
      {endsAt === null ? null : <ExamTimer endsAt={endsAt} onExpire={submit} />}
      <ol className="exam-questions">
        {exam.questions.map((question, index) => (
          <li key={question.id} className="exam-question">
            <div className="muted exam-topic">
              <span>Topic: </span>
              <CardContent markdown={question.topic} className="inline" />
            </div>
            <CardContent markdown={question.prompt} />
            <label className="field">
              <span>Answer to question {index + 1}</span>
              <textarea
                rows={3}
                maxLength={20_000}
                readOnly={busy || closed}
                value={answers[question.id] ?? ""}
                onChange={(event) => {
                  const value = event.target.value;
                  setAnswers((current) => ({ ...current, [question.id]: value }));
                }}
              />
            </label>
          </li>
        ))}
      </ol>
      <ErrorText message={error} />
      {closed ? (
        <div>
          <button type="button" className="button primary" onClick={onAgain}>
            Take another exam
          </button>
        </div>
      ) : (
        <div className="row">
          <button type="submit" className="button primary" aria-disabled={busy}>
            {busy ? "Grading…" : "Submit for grading"}
          </button>
          <span className="muted" role="status">
            {busy
              ? "The AI is grading your answers."
              : unanswered > 0
                ? `${String(unanswered)} unanswered question${unanswered === 1 ? "" : "s"} will score zero.`
                : ""}
          </span>
        </div>
      )}
    </form>
  );
}

function ExamResultView({
  exam,
  result,
  answers,
  onAgain,
}: {
  exam: MockExam;
  result: ExamResult;
  answers: Record<string, string>;
  onAgain: () => void;
}) {
  const headingRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    headingRef.current?.focus();
  }, []);
  const scores = new Map(result.question_scores.map((s) => [s.question_id, s.score]));
  return (
    <div className="stack">
      <h2 tabIndex={-1} ref={headingRef}>
        Your score: {percent(result.overall_score)}
      </h2>
      <AiLabel>Graded by the AI. Compare with your cards before you rely on it.</AiLabel>
      <h3>By topic</h3>
      <div className="table-wrap">
        <table className="browser">
          <thead>
            <tr>
              <th scope="col">Topic</th>
              <th scope="col">Questions</th>
              <th scope="col">Score</th>
            </tr>
          </thead>
          <tbody>
            {result.topics.map((topic, index) => (
              // Topics are model text and may repeat; the row order is stable.
              <tr key={index}>
                <td>
                  <CardContent markdown={topic.topic} className="inline" />
                </td>
                <td>{topic.question_count}</td>
                <td>{percent(topic.score)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h3>Question by question</h3>
      <ol className="exam-questions">
        {exam.questions.map((question) => {
          const given = (answers[question.id] ?? "").trim();
          return (
            <li key={question.id} className="exam-question">
              <CardContent markdown={question.prompt} />
              <p className="muted">Your answer:</p>
              {given === "" ? <p className="muted">No answer</p> : <p className="typed-answer">{given}</p>}
              <p>Score: {percent(scores.get(question.id) ?? 0)}</p>
            </li>
          );
        })}
      </ol>
      <div className="row">
        <button type="button" className="button primary" onClick={onAgain}>
          Take another exam
        </button>
        <Link className="button" to={`/decks/${exam.deck_id}/study`}>
          Study this deck
        </Link>
      </div>
    </div>
  );
}

type Stage =
  | { name: "setup" }
  | { name: "exam"; exam: MockExam }
  | { name: "result"; exam: MockExam; result: ExamResult; answers: Record<string, string> };

export function MockExamPage() {
  const { deckId = "" } = useParams();
  useDocumentTitle("Mock exam");
  const [stage, setStage] = useState<Stage>({ name: "setup" });

  const onGraded = useCallback(
    (exam: MockExam, result: ExamResult, answers: Record<string, string>) => {
      setStage({ name: "result", exam, result, answers });
    },
    [],
  );

  return (
    <section>
      <nav aria-label="Breadcrumb" className="breadcrumb">
        <Link to={`/decks/${deckId}`}>Back to deck</Link>
      </nav>
      <h1>Mock exam</h1>
      {stage.name === "setup" ? (
        <SetupForm
          deckId={deckId}
          onReady={(exam) => {
            setStage({ name: "exam", exam });
          }}
        />
      ) : stage.name === "exam" ? (
        <ExamForm
          exam={stage.exam}
          onGraded={onGraded}
          onAgain={() => {
            setStage({ name: "setup" });
          }}
        />
      ) : (
        <ExamResultView
          exam={stage.exam}
          result={stage.result}
          answers={stage.answers}
          onAgain={() => {
            setStage({ name: "setup" });
          }}
        />
      )}
    </section>
  );
}
