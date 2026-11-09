// SPDX-License-Identifier: AGPL-3.0-only
// What changed in a goal's plan since it was last accepted, and why (F-PLAN-3).

import { plans, type ChangeReason, type PlanChange } from "../api/studyProgress";
import { ErrorText } from "../components/ui";
import { useSubmit } from "../lib/useSubmit";

function cards(count: number): string {
  return `${String(count)} card${count === 1 ? "" : "s"}`;
}

function days(count: number): string {
  return `${String(count)} day${count === 1 ? "" : "s"}`;
}

const REASONS: Record<ChangeReason, (count: number) => string> = {
  cards_added: (n) => `${cards(n)} added to the goal's decks`,
  cards_removed: (n) => `${cards(n)} removed or suspended`,
  cards_started: (n) => `${cards(n)} studied for the first time`,
  missed_days: (n) => `${days(n)} without reviews`,
  days_passed: (n) => `${days(n)} closer to the exam`,
  exam_date_changed: () => "The exam date changed",
  target_changed: () => "The target grade changed",
};

export function reasonText(reason: ChangeReason, count: number): string {
  return REASONS[reason](count);
}

export function PlanChanges({
  goalId,
  change,
  onAccepted,
}: {
  goalId: string;
  change: PlanChange;
  onAccepted: () => void;
}) {
  const submit = useSubmit();
  if (!change.changed) {
    return null;
  }
  const since = new Date(change.since).toLocaleDateString(undefined, { day: "numeric", month: "long" });
  return (
    <section className="panel" aria-labelledby="plan-changes">
      <h2 id="plan-changes">What changed since {since}</h2>
      <p>
        Daily load: {cards(change.daily_load_before)} before, {cards(change.daily_load_after)} now.
      </p>
      <ul className="plain-list">
        {change.reasons.map(({ reason, count }) => (
          <li key={reason}>{reasonText(reason, count)}.</li>
        ))}
      </ul>
      <ErrorText message={submit.error} />
      <button
        type="button"
        className="button"
        disabled={submit.busy}
        onClick={() => {
          void submit.run(async () => {
            await plans.accept(goalId);
            onAccepted();
          });
        }}
      >
        Accept the new plan
      </button>
    </section>
  );
}
