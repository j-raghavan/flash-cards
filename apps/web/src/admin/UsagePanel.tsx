// SPDX-License-Identifier: AGPL-3.0-only
// AI usage and cost for a month, by user and by provider and model (F-ADMIN-4).
// Each chart has a native tooltip per bar, a one-line summary for screen
// readers and a table with every number, like the progress charts.

import { useState } from "react";

import {
  PERIOD_PATTERN,
  adminUsage,
  currentPeriod,
  formatUsd,
  periodLabel,
  shiftPeriod,
  type UsageReport,
} from "../api/adminUsage";
import { admin } from "../api/endpoints";
import { ErrorText, Loading } from "../components/ui";
import { useResource } from "../lib/useResource";

function count(n: number): string {
  return n.toLocaleString();
}

interface Bar {
  key: string;
  label: string;
  value: number;
  tooltip: string;
}

export const CHART_BARS = 10;
const CHART_W = 600;
const ROW_H = 26;
const LABEL_W = 190;
const VALUE_W = 110;

function shorten(text: string, max = 28): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

/** Horizontal bars, largest first; only the first CHART_BARS are drawn, the table has all. */
function UsageBars({ bars, format }: { bars: Bar[]; format: (value: number) => string }) {
  const drawn = bars.slice(0, CHART_BARS);
  const max = Math.max(1, ...drawn.map((b) => b.value));
  const innerW = CHART_W - LABEL_W - VALUE_W;
  const height = drawn.length * ROW_H;
  return (
    <svg className="bar-chart" viewBox={`0 0 ${String(CHART_W)} ${String(height)}`} aria-hidden="true">
      {drawn.map((bar, index) => {
        const y = index * ROW_H;
        const w = Math.max(bar.value > 0 ? 2 : 0, (bar.value / max) * innerW);
        return (
          <g key={bar.key}>
            <text className="axis" x={LABEL_W - 8} y={y + ROW_H / 2 + 4} textAnchor="end">
              {shorten(bar.label)}
            </text>
            <rect className="bar" x={LABEL_W} y={y + 4} width={w} height={ROW_H - 8} rx={2}>
              <title>{bar.tooltip}</title>
            </rect>
            <text className="axis" x={LABEL_W + w + 6} y={y + ROW_H / 2 + 4}>
              {format(bar.value)}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

interface Row {
  key: string;
  label: string;
  calls: number;
  input_tokens: number;
  output_tokens: number;
  cost_micro_usd: number;
  /** AI generation pages, for rows that count them (users). */
  pages?: number;
}

function UsageFigure({
  title,
  head,
  rows,
  priced,
}: {
  title: string;
  head: string;
  rows: Row[];
  priced: boolean;
}) {
  // Cost when prices are set; otherwise tokens are the only measure there is.
  const measure = (row: Row): number => (priced ? row.cost_micro_usd : row.input_tokens + row.output_tokens);
  const format = priced ? formatUsd : (n: number) => `${count(n)} tokens`;
  const sorted = [...rows].sort((a, b) => measure(b) - measure(a) || a.label.localeCompare(b.label));
  const top = sorted[0];
  const withPages = rows.some((row) => row.pages !== undefined);
  const summary =
    top === undefined
      ? "No AI use this month."
      : `${String(sorted.length)} ${sorted.length === 1 ? "entry" : "entries"} by ${priced ? "cost" : "tokens"}; the largest is ${top.label} with ${format(measure(top))}.`;
  return (
    <figure className="chart">
      <figcaption>
        <h3>{title}</h3>
      </figcaption>
      <p className="visually-hidden">{summary}</p>
      <UsageBars
        format={format}
        bars={sorted.map((row) => ({
          key: row.key,
          label: row.label,
          value: measure(row),
          tooltip: `${row.label}: ${row.pages === undefined ? "" : `${count(row.pages)} AI pages, `}${count(row.calls)} calls, ${count(row.input_tokens)} input and ${count(row.output_tokens)} output tokens, ${formatUsd(row.cost_micro_usd)}`,
        }))}
      />
      <div className="table-wrap">
        <table className="browser">
          <caption className="visually-hidden">{title}</caption>
          <thead>
            <tr>
              <th scope="col">{head}</th>
              {withPages ? <th scope="col">AI pages</th> : null}
              <th scope="col">Calls</th>
              <th scope="col">Input tokens</th>
              <th scope="col">Output tokens</th>
              <th scope="col">Estimated cost (USD)</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((row) => (
              <tr key={row.key}>
                <th scope="row">{row.label}</th>
                {/* Rows in one table all count pages, or none do. */}
                {row.pages === undefined ? null : <td>{count(row.pages)}</td>}
                <td>{count(row.calls)}</td>
                <td>{count(row.input_tokens)}</td>
                <td>{count(row.output_tokens)}</td>
                <td>{formatUsd(row.cost_micro_usd)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </figure>
  );
}

function Report({ report, emails }: { report: UsageReport; emails: Map<string, string> | undefined }) {
  const calls = report.by_provider.reduce((sum, p) => sum + p.calls, 0);
  const input = report.by_provider.reduce((sum, p) => sum + p.input_tokens, 0);
  const output = report.by_provider.reduce((sum, p) => sum + p.output_tokens, 0);
  const pages = report.by_user.reduce((sum, u) => sum + u.pages, 0);
  // Prices default to zero, which records tokens but no cost (ADR 0016).
  const unpriced = input + output > 0 && report.cost_micro_usd === 0;
  const name = (userId: string): string =>
    emails === undefined ? userId : (emails.get(userId) ?? `Deleted account ${userId}`);

  return (
    <>
      <div className="stats">
        <dl>
          <div>
            <dt>Estimated cost</dt>
            <dd>{formatUsd(report.cost_micro_usd)}</dd>
          </div>
          <div>
            <dt>Spend cap</dt>
            <dd>{report.spend_cap_micro_usd === null ? "No cap" : formatUsd(report.spend_cap_micro_usd)}</dd>
          </div>
          <div>
            <dt>AI pages</dt>
            <dd>{count(pages)}</dd>
          </div>
          <div>
            <dt>Calls</dt>
            <dd>{count(calls)}</dd>
          </div>
          <div>
            <dt>Input tokens</dt>
            <dd>{count(input)}</dd>
          </div>
          <div>
            <dt>Output tokens</dt>
            <dd>{count(output)}</dd>
          </div>
        </dl>
      </div>
      {unpriced ? (
        <div className="panel" role="note">
          <p>
            <strong>Prices are not configured.</strong> Tokens are recorded but every cost shows as $0.00, and the
            spend cap has no effect. Set <code>FLASHCARDS_AI_INPUT_USD_PER_MTOK</code> and{" "}
            <code>FLASHCARDS_AI_OUTPUT_USD_PER_MTOK</code> on the server to estimate costs.
          </p>
        </div>
      ) : null}
      {calls === 0 && report.by_user.length === 0 ? (
        <p className="empty">No AI use in {periodLabel(report.period)}.</p>
      ) : (
        <div className="charts">
          <UsageFigure
            title="Usage by user"
            head="User"
            priced={!unpriced}
            rows={report.by_user.map((u) => ({ ...u, key: u.user_id, label: name(u.user_id) }))}
          />
          <UsageFigure
            title="Usage by provider and model"
            head="Provider and model"
            priced={!unpriced}
            rows={report.by_provider.map((p) => ({
              ...p,
              key: `${p.provider}/${p.model}`,
              label: `${p.provider} ${p.model}`,
            }))}
          />
        </div>
      )}
    </>
  );
}

export function UsagePanel() {
  const latest = currentPeriod();
  const [period, setPeriod] = useState(latest);
  const [draft, setDraft] = useState(latest);
  const [touched, setTouched] = useState(false);
  const report = useResource(() => adminUsage.usage(period), `usage:${period}`);
  const users = useResource(() => admin.users(), "admin:users");
  const emails = users.data === undefined ? undefined : new Map(users.data.map((u) => [u.id, u.email]));
  const invalid = !PERIOD_PATTERN.test(draft);

  const go = (next: string): void => {
    setDraft(next);
    setTouched(false);
    setPeriod(next);
  };

  return (
    <>
      <div className="inline-form">
        <button
          type="button"
          className="button"
          onClick={() => {
            go(shiftPeriod(period, -1));
          }}
        >
          Previous month
        </button>
        <label className="field">
          <span>Month (YYYY-MM)</span>
          <input
            type="month"
            max={latest}
            value={draft}
            aria-invalid={touched && invalid}
            aria-describedby={touched && invalid ? "usage-period-error" : undefined}
            onChange={(event) => {
              const value = event.target.value;
              setDraft(value);
              if (PERIOD_PATTERN.test(value)) {
                setPeriod(value);
              }
            }}
            onBlur={() => {
              setTouched(true);
            }}
          />
        </label>
        <button
          type="button"
          className="button"
          disabled={period >= latest}
          onClick={() => {
            go(shiftPeriod(period, 1));
          }}
        >
          Next month
        </button>
      </div>
      {touched && invalid ? (
        <p className="error" id="usage-period-error">
          Enter a month as YYYY-MM, such as {latest}.
        </p>
      ) : null}
      <h2>{periodLabel(period)}</h2>
      {users.error === null ? null : (
        <p className="muted">The user list could not be loaded, so users are shown by id. ({users.error})</p>
      )}
      <ErrorText message={report.error} />
      {report.data === undefined ? (
        report.error === null ? (
          <Loading />
        ) : null
      ) : (
        <Report report={report.data} emails={emails} />
      )}
    </>
  );
}
