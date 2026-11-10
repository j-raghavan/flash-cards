// SPDX-License-Identifier: AGPL-3.0-only
// The guided first run (F-ONB-1): a deck, material for it, checking the cards,
// an exam date and grade, then a first session. Every step can be skipped, and
// so can the whole flow; where it stands is remembered per user on this device
// (see state.ts). It links into the existing screens rather than copying them,
// and the home page offers a way back. Loaded on first visit.

import { useEffect, useRef, useState, type ReactNode, type SubmitEvent } from "react";
import { Link, useNavigate } from "react-router";

import { decks, goals } from "../api/endpoints";
import type { Deck } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { ErrorText, Loading } from "../components/ui";
import { deckOptions, ownedDecks } from "../lib/deckTree";
import { useDocumentTitle } from "../lib/useDocumentTitle";
import { useResource } from "../lib/useResource";
import { useSubmit } from "../lib/useSubmit";
import { tomorrow } from "../pages/GoalsPage";
import {
  ONBOARDING_STEPS,
  finishOnboarding,
  nextStep,
  readOnboarding,
  saveOnboarding,
  startOnboarding,
  stepNumber,
  type OnboardingState,
  type OnboardingStep,
} from "./state";

const STEP_LABELS: Record<OnboardingStep, string> = {
  deck: "Start a deck",
  material: "Add material",
  verify: "Check your cards",
  goal: "Set your exam",
  study: "First session",
};

function StepFrame({ step, children, onSkip }: { step: OnboardingStep; children: ReactNode; onSkip: () => void }) {
  const headingRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    // A new step replaces the old one; move focus to it so it is announced.
    headingRef.current?.focus();
  }, [step]);
  return (
    <section className="panel" aria-labelledby="onboarding-step">
      <h2 id="onboarding-step" tabIndex={-1} ref={headingRef}>
        {STEP_LABELS[step]}
      </h2>
      {children}
      <p>
        <button type="button" className="link-button" onClick={onSkip}>
          Skip this step
        </button>
      </p>
    </section>
  );
}

function DeckStep({ all, onChosen }: { all: Deck[]; onChosen: (deckId: string) => void }) {
  const [name, setName] = useState("");
  const [existing, setExisting] = useState("");
  const submit = useSubmit();
  const create = (event: SubmitEvent): void => {
    event.preventDefault();
    void submit.run(async () => {
      const deck = await decks.create(name.trim(), null);
      onChosen(deck.id);
    });
  };
  return (
    <>
      <p>A deck holds the cards for one subject or exam. Name it after what you are studying.</p>
      <form className="inline-form" onSubmit={create} aria-label="Create a deck">
        <label className="field grow">
          <span>Deck name</span>
          <input
            required
            maxLength={500}
            value={name}
            placeholder="e.g. Organic Chemistry"
            onChange={(event) => {
              setName(event.target.value);
            }}
          />
        </label>
        <button type="submit" className="button primary" disabled={submit.busy || name.trim() === ""}>
          Create deck
        </button>
      </form>
      {all.length > 0 ? (
        <div className="inline-form">
          <label className="field grow">
            <span>Or use a deck you already have</span>
            <select
              value={existing}
              onChange={(event) => {
                setExisting(event.target.value);
              }}
            >
              <option value="">Choose a deck</option>
              {deckOptions(all).map((option) => (
                <option key={option.id} value={option.id}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="button"
            disabled={existing === ""}
            onClick={() => {
              onChosen(existing);
            }}
          >
            Use this deck
          </button>
        </div>
      ) : null}
      <ErrorText message={submit.error} />
    </>
  );
}

function MaterialStep({ deckId, onLeave, onNext }: { deckId: string | null; onLeave: () => void; onNext: () => void }) {
  return (
    <>
      <p>Bring in what you study from. Come back here from the home page when you are done.</p>
      <ul className="plain-list">
        {deckId === null ? null : (
          <>
            <li>
              <Link to={`/decks/${encodeURIComponent(deckId)}/generate`} onClick={onLeave}>
                Generate cards from a PDF or image
              </Link>{" "}
              <span className="muted">The AI drafts cards; you approve them.</span>
            </li>
            <li>
              <Link to={`/decks/${encodeURIComponent(deckId)}/notes/new`} onClick={onLeave}>
                Write a card yourself
              </Link>
            </li>
          </>
        )}
        <li>
          <Link to="/import" onClick={onLeave}>
            Import a deck from Anki
          </Link>
        </li>
      </ul>
      {deckId === null ? <p className="muted">Start a deck first to generate or write cards for it.</p> : null}
      <button type="button" className="button primary" onClick={onNext}>
        I have added cards
      </button>
    </>
  );
}

function VerifyStep({ deckId, onNext }: { deckId: string | null; onNext: () => void }) {
  return (
    <>
      <p>
        Generated cards are drafts. Read each one on its generation page, edit what is off and approve the rest. Nothing
        joins your deck until you approve it.
      </p>
      {deckId === null ? null : (
        <p>
          <Link to={`/decks/${encodeURIComponent(deckId)}`}>Open the deck</Link> to see its cards and recent
          generations.
        </p>
      )}
      <button type="button" className="button primary" onClick={onNext}>
        My cards look right
      </button>
    </>
  );
}

function GoalStep({ deck, onNext }: { deck: Deck | null; onNext: () => void }) {
  const [examDate, setExamDate] = useState("");
  const [grade, setGrade] = useState(70);
  const submit = useSubmit();
  if (deck === null) {
    return (
      <p className="muted">
        A goal needs a deck to plan for. You can set one later on the <Link to="/goals">goals page</Link>.
      </p>
    );
  }
  const save = (event: SubmitEvent): void => {
    event.preventDefault();
    void submit.run(async () => {
      await goals.create({
        name: `${deck.name} exam`.slice(0, 200),
        deck_ids: [deck.id],
        // Local midnight of the exam day.
        exam_date: new Date(`${examDate}T00:00:00`).toISOString(),
        desired_grade: grade / 100,
      });
      onNext();
    });
  };
  return (
    <form className="stack" onSubmit={save} aria-label="Set your exam">
      <p>With an exam date and the grade you want, the plan works out how much to study each day.</p>
      <label className="field">
        <span>Exam date</span>
        <input
          type="date"
          required
          min={tomorrow()}
          value={examDate}
          onChange={(event) => {
            setExamDate(event.target.value);
          }}
        />
      </label>
      <div className="field">
        <label htmlFor="onboarding-grade">Target grade: {grade}%</label>
        <input
          id="onboarding-grade"
          type="range"
          min={0}
          max={100}
          step={5}
          value={grade}
          aria-valuetext={`${String(grade)}%`}
          onChange={(event) => {
            setGrade(Number(event.target.value));
          }}
        />
      </div>
      <ErrorText message={submit.error} />
      <div>
        <button type="submit" className="button primary" disabled={examDate === "" || submit.busy}>
          Save my exam
        </button>
      </div>
    </form>
  );
}

function StudyStep({ deckId, onFinish }: { deckId: string | null; onFinish: (to: string) => void }) {
  return (
    <>
      <p>
        Study a few cards now. Rate each answer honestly: the schedule uses your ratings to bring cards back just before
        you would forget them.
      </p>
      <div className="row">
        {deckId === null ? null : (
          <button
            type="button"
            className="button primary"
            onClick={() => {
              onFinish(`/decks/${encodeURIComponent(deckId)}/study`);
            }}
          >
            Start my first session
          </button>
        )}
        <button
          type="button"
          className={deckId === null ? "button primary" : "button"}
          onClick={() => {
            onFinish("/");
          }}
        >
          Finish setup
        </button>
      </div>
    </>
  );
}

export function OnboardingPage() {
  useDocumentTitle("Getting started");
  const { user } = useAuth();
  // The signed-in frame sends a signed-out visitor to sign-in first.
  return user === null ? null : <Onboarding userId={user.id} />;
}

function Onboarding({ userId }: { userId: string }) {
  const navigate = useNavigate();
  // Never started: start now. Finished: say so rather than starting over, since
  // Back after finishing (or an old link) lands here too; the account page and the
  // button below start it again on purpose.
  const [state, setState] = useState<OnboardingState>(() => readOnboarding(userId) ?? startOnboarding(userId));
  const all = useResource(() => decks.list(), "decks");

  const update = (next: OnboardingState): void => {
    saveOnboarding(userId, next);
    setState(next);
  };
  const finish = (to: string): void => {
    finishOnboarding(userId);
    // Replace, so Back does not return to a finished setup.
    void navigate(to, { replace: true });
  };
  const advance = (patch: Partial<OnboardingState> = {}): void => {
    const following = nextStep(state.step);
    if (following === null) {
      finish("/");
      return;
    }
    update({ ...state, ...patch, step: following });
  };

  if (state.status === "done") {
    return (
      <section>
        <h1>Getting started</h1>
        <p>You have finished the guided setup.</p>
        <div className="row">
          <Link className="button primary" to="/">
            Go to your decks
          </Link>
          <button
            type="button"
            className="button"
            onClick={() => {
              setState(startOnboarding(userId));
            }}
          >
            Run it again
          </button>
        </div>
      </section>
    );
  }
  if (all.data === undefined) {
    return all.error === null ? <Loading /> : <ErrorText message={all.error} />;
  }
  // A deck deleted since it was chosen no longer counts.
  const deck = all.data.find((d) => d.id === state.deckId) ?? null;
  const deckId = deck?.id ?? null;
  const step = state.step;

  let body: ReactNode;
  switch (step) {
    case "deck":
      body = (
        <DeckStep
          all={ownedDecks(all.data)}
          onChosen={(id) => {
            all.reload();
            advance({ deckId: id });
          }}
        />
      );
      break;
    case "material":
      body = (
        <MaterialStep
          deckId={deckId}
          onLeave={() => {
            // Coming back lands on the next step.
            update({ ...state, step: "verify" });
          }}
          onNext={() => {
            advance();
          }}
        />
      );
      break;
    case "verify":
      body = (
        <VerifyStep
          deckId={deckId}
          onNext={() => {
            advance();
          }}
        />
      );
      break;
    case "goal":
      body = (
        <GoalStep
          deck={deck}
          onNext={() => {
            advance();
          }}
        />
      );
      break;
    case "study":
      body = <StudyStep deckId={deckId} onFinish={finish} />;
      break;
  }

  return (
    <section>
      <h1>Getting started</h1>
      <p className="muted">
        Five short steps to your first study session. Skip any of them; you can run this again from your account page.
      </p>
      <ol className="steps" aria-label="Setup steps">
        {ONBOARDING_STEPS.map((name) => (
          <li key={name} aria-current={name === step ? "step" : undefined}>
            {STEP_LABELS[name]}
          </li>
        ))}
      </ol>
      <p className="muted" aria-live="polite">
        Step {stepNumber(step)} of {ONBOARDING_STEPS.length}
      </p>
      <StepFrame
        step={step}
        onSkip={() => {
          advance();
        }}
      >
        {body}
      </StepFrame>
      <p>
        <button
          type="button"
          className="link-button"
          onClick={() => {
            finish("/");
          }}
        >
          Skip the setup
        </button>
      </p>
    </section>
  );
}
