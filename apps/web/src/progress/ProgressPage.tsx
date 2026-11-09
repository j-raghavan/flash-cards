// SPDX-License-Identifier: AGPL-3.0-only
// Progress (F-PROG-1, F-PROG-3): today's count against the daily goal and the
// streak, the study settings that define a day, and the review heatmap,
// retention curve and forecast, overall or for one deck.

import { useState, type SubmitEvent } from "react";

import { decks } from "../api/endpoints";
import {
  MAX_DAILY_GOAL,
  MIN_DAILY_GOAL,
  progress,
  type StudySettings,
  type Today,
} from "../api/studyProgress";
import { ErrorText, Loading } from "../components/ui";
import { deckOptions } from "../lib/deckTree";
import { useDocumentTitle } from "../lib/useDocumentTitle";
import { useResource } from "../lib/useResource";
import { useSubmit } from "../lib/useSubmit";
import { Forecast, Heatmap, RetentionCurve } from "./charts";

const RANGES = [
  { days: 30, label: "30 days" },
  { days: 90, label: "90 days" },
  { days: 365, label: "A year" },
];
const FORECAST_DAYS = 30;

/** Every IANA zone this browser knows, with the saved one always included. */
export function timeZones(current: string): string[] {
  const known = Intl.supportedValuesOf("timeZone");
  return known.includes(current) ? known : [current, ...known];
}

export function deviceTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone;
}

function TodayPanel({ today }: { today: Today }) {
  return (
    <section className="panel" aria-labelledby="today-heading">
      <h2 id="today-heading">Today</h2>
      <label className="field">
        <span>
          {today.reviews} of {today.daily_goal} cards
        </span>
        <progress max={today.daily_goal} value={Math.min(today.reviews, today.daily_goal)} />
      </label>
      {today.goal_met ? (
        <p className="celebration" role="status">
          Daily goal reached. Well done.
        </p>
      ) : null}
      <p>
        {today.streak === 0
          ? "Meet today's goal to start a streak."
          : `Streak: ${String(today.streak)} day${today.streak === 1 ? "" : "s"} in a row.`}
      </p>
    </section>
  );
}

function SettingsForm({ settings, onSaved }: { settings: StudySettings; onSaved: (saved: StudySettings) => void }) {
  const [goal, setGoal] = useState(String(settings.daily_goal));
  const [zone, setZone] = useState(settings.time_zone);
  const [saved, setSaved] = useState(false);
  const submit = useSubmit();
  const device = deviceTimeZone();
  const goalNumber = Number(goal);
  const valid = Number.isInteger(goalNumber) && goalNumber >= MIN_DAILY_GOAL && goalNumber <= MAX_DAILY_GOAL;

  const onSubmit = (event: SubmitEvent): void => {
    event.preventDefault();
    setSaved(false);
    void submit.run(async () => {
      onSaved(await progress.updateSettings({ daily_goal: goalNumber, time_zone: zone }));
      setSaved(true);
    });
  };

  return (
    <form className="inline-form" onSubmit={onSubmit} aria-label="Study settings">
      <label className="field">
        <span>Daily goal (cards)</span>
        <input
          type="number"
          min={MIN_DAILY_GOAL}
          max={MAX_DAILY_GOAL}
          required
          value={goal}
          onChange={(event) => {
            setGoal(event.target.value);
            setSaved(false);
          }}
        />
      </label>
      <label className="field grow">
        <span>Time zone (when a day starts)</span>
        <select
          value={zone}
          onChange={(event) => {
            setZone(event.target.value);
            setSaved(false);
          }}
        >
          {timeZones(settings.time_zone).map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
      </label>
      {zone === device ? null : (
        <button
          type="button"
          className="link-button"
          onClick={() => {
            setZone(device);
            setSaved(false);
          }}
        >
          Use this device's zone ({device})
        </button>
      )}
      <button type="submit" className="button primary" disabled={!valid || submit.busy}>
        Save
      </button>
      <span role="status" className="muted">
        {saved ? "Saved." : ""}
      </span>
      <ErrorText message={submit.error} />
    </form>
  );
}

export function ProgressPage() {
  useDocumentTitle("Progress");
  const [generation, setGeneration] = useState(0);
  const today = useResource(() => progress.today(), `today:${String(generation)}`);
  const settings = useResource(() => progress.settings(), "settings");
  const all = useResource(() => decks.list(), "decks");
  const [deckId, setDeckId] = useState("");
  const [days, setDays] = useState(90);
  const stats = useResource(
    () => progress.stats(deckId === "" ? null : deckId, days, FORECAST_DAYS),
    `stats:${deckId}:${String(days)}:${String(generation)}`,
  );

  return (
    <section>
      <h1>Progress</h1>
      <ErrorText message={today.error} />
      {today.data === undefined ? today.error === null ? <Loading /> : null : <TodayPanel today={today.data} />}

      <h2>Study settings</h2>
      <ErrorText message={settings.error} />
      {settings.data === undefined ? null : (
        <SettingsForm
          settings={settings.data}
          onSaved={(saved) => {
            settings.setData(saved);
            // A new goal or zone changes today's count and how days are grouped.
            setGeneration((n) => n + 1);
          }}
        />
      )}

      <h2>Statistics</h2>
      <div className="filters">
        <label className="field">
          <span>Deck</span>
          <select
            value={deckId}
            onChange={(event) => {
              setDeckId(event.target.value);
            }}
          >
            <option value="">All decks</option>
            {deckOptions(all.data ?? []).map((option) => (
              <option key={option.id} value={option.id}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Range</span>
          <select
            value={days}
            onChange={(event) => {
              setDays(Number(event.target.value));
            }}
          >
            {RANGES.map((range) => (
              <option key={range.days} value={range.days}>
                {range.label}
              </option>
            ))}
          </select>
        </label>
      </div>
      <ErrorText message={stats.error} />
      {stats.data === undefined ? (
        stats.error === null ? (
          <Loading label="Loading statistics…" />
        ) : null
      ) : (
        <div className="charts" aria-busy={stats.loading}>
          <Heatmap days={stats.data.heatmap} />
          <RetentionCurve points={stats.data.retention} />
          <Forecast days={stats.data.forecast} />
        </div>
      )}
    </section>
  );
}
