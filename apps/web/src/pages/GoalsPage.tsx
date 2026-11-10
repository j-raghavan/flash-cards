// SPDX-License-Identifier: AGPL-3.0-only
// Exam goals and their study plans (F-PLAN-1..3): a goal ties decks to an exam
// date and a target grade; its plan says how many new cards and reviews a day
// keep it on track, how ready the decks are, and what changed after a replan.

import { useState, type SubmitEvent } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { describeError } from "../api/client";
import { decks, goals } from "../api/endpoints";
import { plans } from "../api/studyProgress";
import type { Deck, Goal, GoalInput, StudyPlan } from "../api/types";
import { ConfirmButton, ErrorText, Loading, ProficiencyRing } from "../components/ui";
import { deckOptions, ownedDecks } from "../lib/deckTree";
import { useDocumentTitle } from "../lib/useDocumentTitle";
import { useResource } from "../lib/useResource";
import { useSubmit } from "../lib/useSubmit";
import { PlanChanges } from "../progress/PlanChanges";

// The server maps the grade slider (0..1) onto a review retention target in
// this band (domain/goals.py), shown here so the slider explains itself.
const MIN_RETENTION = 0.8;
const MAX_RETENTION = 0.97;

export function gradeFromRetention(retention: number): number {
  const grade = (retention - MIN_RETENTION) / (MAX_RETENTION - MIN_RETENTION);
  return Math.round(Math.max(0, Math.min(1, grade)) * 100);
}

function retentionForGrade(grade: number): number {
  return Math.round((MIN_RETENTION + ((MAX_RETENTION - MIN_RETENTION) * grade) / 100) * 100);
}

/** A local calendar date (yyyy-mm-dd) for a date input. */
export function localDate(iso: string): string {
  const date = new Date(iso);
  const pad = (n: number): string => String(n).padStart(2, "0");
  return `${String(date.getFullYear())}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/** Tomorrow as a local calendar date: an exam must lie in the future. */
export function tomorrow(now = new Date()): string {
  return localDate(new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1, 12).toISOString());
}

/** Only what an edit changed, so renaming a goal on its exam day does not resend the date. */
export function goalChanges(goal: Goal, input: GoalInput): Partial<GoalInput> {
  const changes: Partial<GoalInput> = {};
  if (input.name !== goal.name) {
    changes.name = input.name;
  }
  if ([...input.deck_ids].sort().join(" ") !== [...goal.deck_ids].sort().join(" ")) {
    changes.deck_ids = input.deck_ids;
  }
  if (localDate(input.exam_date) !== localDate(goal.exam_date)) {
    changes.exam_date = input.exam_date;
  }
  if (Math.round(input.desired_grade * 100) !== gradeFromRetention(goal.target_retention)) {
    changes.desired_grade = input.desired_grade;
  }
  return changes;
}

function longDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "long", year: "numeric" });
}

function GoalForm({
  all,
  initial,
  submitLabel,
  onSubmit,
  onCancel,
}: {
  all: Deck[];
  initial?: Goal;
  submitLabel: string;
  onSubmit: (input: GoalInput) => Promise<void>;
  onCancel?: () => void;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [chosen, setChosen] = useState<string[]>(initial?.deck_ids ?? []);
  const [examDate, setExamDate] = useState(initial === undefined ? "" : localDate(initial.exam_date));
  const [grade, setGrade] = useState(initial === undefined ? 50 : gradeFromRetention(initial.target_retention));
  const submit = useSubmit();
  const ready = name.trim() !== "" && chosen.length > 0 && examDate !== "";

  const send = (event: SubmitEvent): void => {
    event.preventDefault();
    void submit.run(() =>
      onSubmit({
        name: name.trim(),
        deck_ids: chosen,
        // Local midnight of the exam day.
        exam_date: new Date(`${examDate}T00:00:00`).toISOString(),
        desired_grade: grade / 100,
      }),
    );
  };

  return (
    <form className="stack" onSubmit={send} aria-label={submitLabel}>
      <label className="field">
        <span>Goal name</span>
        <input
          required
          maxLength={200}
          value={name}
          placeholder="e.g. Biology final"
          onChange={(event) => {
            setName(event.target.value);
          }}
        />
      </label>
      <fieldset>
        <legend>Decks to study</legend>
        {all.length === 0 ? (
          <p className="muted">
            Create a deck first on the <Link to="/">decks page</Link>.
          </p>
        ) : (
          <div className="chips">
            {deckOptions(all).map((option) => (
              <label key={option.id}>
                <input
                  type="checkbox"
                  checked={chosen.includes(option.id)}
                  onChange={(event) => {
                    setChosen(event.target.checked ? [...chosen, option.id] : chosen.filter((id) => id !== option.id));
                  }}
                />
                {option.label}
              </label>
            ))}
          </div>
        )}
      </fieldset>
      <label className="field">
        <span>Exam date</span>
        <input
          type="date"
          required
          min={initial === undefined ? tomorrow() : undefined}
          value={examDate}
          onChange={(event) => {
            setExamDate(event.target.value);
          }}
        />
      </label>
      <div className="field">
        <label htmlFor="goal-grade">Target grade: {grade}%</label>
        <input
          id="goal-grade"
          type="range"
          min={0}
          max={100}
          step={5}
          value={grade}
          aria-describedby="goal-grade-hint"
          aria-valuetext={`${String(grade)}%`}
          onChange={(event) => {
            setGrade(Number(event.target.value));
          }}
        />
        <small id="goal-grade-hint" className="muted">
          Reviews are scheduled to keep about {retentionForGrade(grade)}% of cards in memory. Higher means more reviews
          a day.
        </small>
      </div>
      <ErrorText message={submit.error} />
      <div className="row">
        <button type="submit" className="button primary" disabled={!ready || submit.busy}>
          {submitLabel}
        </button>
        {onCancel === undefined ? null : (
          <button type="button" className="button" onClick={onCancel}>
            Cancel
          </button>
        )}
      </div>
    </form>
  );
}

export function GoalsPage() {
  useDocumentTitle("Goals");
  const navigate = useNavigate();
  const list = useResource(() => goals.list(), "goals");
  const all = useResource(() => decks.list(), "decks");

  return (
    <section>
      <h1>Goals</h1>
      <p className="muted">Set an exam date and a target grade; the plan works out what to study each day.</p>
      <ErrorText message={list.error} />
      {list.data === undefined ? (
        list.error === null ? (
          <Loading />
        ) : null
      ) : list.data.length === 0 ? (
        <p className="empty">No goals yet. Add your exam date below and the plan works out what to study each day.</p>
      ) : (
        <ul className="deck-tree">
          {list.data.map((goal) => (
            <li key={goal.id}>
              <Link to={`/goals/${goal.id}`}>{goal.name}</Link>{" "}
              <span className="muted">exam on {longDate(goal.exam_date)}</span>
            </li>
          ))}
        </ul>
      )}
      <h2>New goal</h2>
      <ErrorText message={all.error} />
      {all.data === undefined ? null : (
        <GoalForm
          all={ownedDecks(all.data)}
          submitLabel="Create goal"
          onSubmit={async (input) => {
            const created = await goals.create(input);
            void navigate(`/goals/${created.id}`);
          }}
        />
      )}
    </section>
  );
}

function PlanPanel({ plan }: { plan: StudyPlan }) {
  const readiness = Math.round(plan.readiness * 100);
  return (
    <>
      <h2>Today's plan</h2>
      <p className="lead">
        Study {plan.new_per_day} new card{plan.new_per_day === 1 ? "" : "s"} and about {plan.reviews_per_day} review
        {plan.reviews_per_day === 1 ? "" : "s"} a day.
      </p>
      <h2>Readiness</h2>
      <div className="stats">
        <ProficiencyRing value={readiness} label="Readiness" />
        <dl>
          <div>
            <dt>Days left</dt>
            <dd>{plan.days_until_exam}</dd>
          </div>
          <div>
            <dt>Cards</dt>
            <dd>{plan.total_cards}</dd>
          </div>
          <div>
            <dt>Not started</dt>
            <dd>{plan.new_cards}</dd>
          </div>
          <div>
            <dt>In review</dt>
            <dd>{plan.scheduled_cards}</dd>
          </div>
        </dl>
      </div>
      <p role="status">
        {plan.on_track
          ? "On track: following this plan reaches the target by the exam."
          : "Behind: at this pace the target is out of reach by the exam. More time each day, a later date or a lower target would help."}
      </p>
    </>
  );
}

export function GoalPage() {
  const { goalId = "" } = useParams();
  const navigate = useNavigate();
  const goal = useResource(() => goals.get(goalId), `goal:${goalId}`);
  const plan = useResource(() => plans.report(goalId), `plan:${goalId}`);
  const all = useResource(() => decks.list(), "decks");
  const [editing, setEditing] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  useDocumentTitle(goal.data?.name ?? "Goal");

  if (goal.error !== null) {
    return (
      <section>
        <nav aria-label="Breadcrumb" className="breadcrumb">
          <Link to="/goals">Goals</Link>
        </nav>
        <ErrorText message={goal.error} />
      </section>
    );
  }
  if (goal.data === undefined) {
    return <Loading />;
  }
  const current = goal.data;
  const names = new Map((all.data ?? []).map((deck) => [deck.id, deck.name]));

  return (
    <section>
      <nav aria-label="Breadcrumb" className="breadcrumb">
        <Link to="/goals">Goals</Link>
      </nav>
      <h1>{current.name}</h1>
      <p className="muted">
        Exam on {longDate(current.exam_date)}. Target grade {gradeFromRetention(current.target_retention)}%.{" "}
        {current.deck_ids.map((id) => names.get(id) ?? "A deck").join(", ")}.
      </p>

      <ErrorText message={plan.error} />
      {plan.data === undefined ? (
        plan.error === null ? (
          <Loading label="Working out the plan…" />
        ) : null
      ) : (
        <>
          {plan.data.changes == null ? null : (
            <PlanChanges goalId={goalId} change={plan.data.changes} onAccepted={plan.reload} />
          )}
          <PlanPanel plan={plan.data} />
        </>
      )}

      <h2>Manage</h2>
      {editing && all.data !== undefined ? (
        <GoalForm
          all={ownedDecks(all.data)}
          initial={current}
          submitLabel="Save goal"
          onCancel={() => {
            setEditing(false);
          }}
          onSubmit={async (input) => {
            const changes = goalChanges(current, input);
            if (Object.keys(changes).length > 0) {
              goal.setData(await goals.update(goalId, changes));
              plan.reload();
            }
            setEditing(false);
          }}
        />
      ) : (
        <div className="row actions">
          <button
            type="button"
            className="button"
            disabled={all.data === undefined}
            onClick={() => {
              setEditing(true);
            }}
          >
            Edit goal
          </button>
          <ConfirmButton
            label="Delete goal"
            confirmLabel="Delete goal"
            prompt={`Delete the goal “${current.name}”? Its decks and cards stay.`}
            onConfirm={async () => {
              setDeleteError(null);
              try {
                await goals.remove(goalId);
                void navigate("/goals");
              } catch (failure) {
                setDeleteError(describeError(failure));
              }
            }}
          />
        </div>
      )}
      <ErrorText message={deleteError} />
    </section>
  );
}
