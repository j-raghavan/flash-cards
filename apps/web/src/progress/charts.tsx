// SPDX-License-Identifier: AGPL-3.0-only
// Small, dependency-free SVG charts for study statistics (F-PROG-3): a review
// heatmap, a retention curve and a forecast of upcoming reviews. Each mark has
// a native tooltip (an SVG <title>), each chart a one-line summary for screen
// readers, and every chart a table view with the same numbers.

import type { ReactNode } from "react";

import type { ForecastDay, HeatmapDay, RetentionPoint } from "../api/studyProgress";

/** "2026-11-08" as "8 Nov", read as a calendar date (no time zone shift). */
export function shortDate(isoDate: string): string {
  return new Date(`${isoDate}T00:00:00`).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
  });
}

function plural(count: number, word: string): string {
  return `${String(count)} ${word}${count === 1 ? "" : "s"}`;
}

function TableView({ caption, head, rows }: { caption: string; head: [string, string]; rows: [string, string][] }) {
  return (
    <details className="chart-table">
      <summary>Show as a table</summary>
      <table>
        <caption className="visually-hidden">{caption}</caption>
        <thead>
          <tr>
            <th scope="col">{head[0]}</th>
            <th scope="col">{head[1]}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([label, value]) => (
            <tr key={label}>
              <th scope="row">{label}</th>
              <td>{value}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}

function Figure({ title, summary, children }: { title: string; summary: string; children: ReactNode }) {
  return (
    <figure className="chart">
      <figcaption>
        <h3>{title}</h3>
      </figcaption>
      <p className="visually-hidden">{summary}</p>
      {children}
    </figure>
  );
}

// --- heatmap ---

const CELL = 12;
const GAP = 2;

/** 0 for no reviews, else 1 to 4 by share of the busiest day. */
export function heatLevel(reviews: number, max: number): number {
  if (reviews <= 0 || max <= 0) {
    return 0;
  }
  return Math.min(4, Math.ceil((reviews / max) * 4));
}

/** Monday 0 to Sunday 6. */
function weekday(isoDate: string): number {
  return (new Date(`${isoDate}T00:00:00Z`).getUTCDay() + 6) % 7;
}

function NoData({ title }: { title: string }) {
  return (
    <Figure title={title} summary="No data in this range yet.">
      <p className="empty">No data in this range yet.</p>
    </Figure>
  );
}

export function Heatmap({ days }: { days: HeatmapDay[] }) {
  const first = days[0];
  if (first === undefined) {
    return <NoData title="Reviews per day" />;
  }
  const max = Math.max(0, ...days.map((d) => d.reviews));
  const total = days.reduce((sum, d) => sum + d.reviews, 0);
  const studied = days.filter((d) => d.reviews > 0).length;
  const offset = weekday(first.date);
  const width = Math.ceil((days.length + offset) / 7) * (CELL + GAP);
  const height = 7 * (CELL + GAP);
  return (
    <Figure
      title="Reviews per day"
      summary={`${plural(total, "review")} on ${plural(studied, "day")} of the last ${plural(days.length, "day")}.`}
    >
      <div className="chart-scroll">
        <svg
          className="heatmap"
          width={width}
          height={height}
          viewBox={`0 0 ${String(width)} ${String(height)}`}
          aria-hidden="true"
        >
          {days.map((day, index) => {
            const slot = index + offset;
            return (
              <rect
                key={day.date}
                className={`heat heat-${String(heatLevel(day.reviews, max))}`}
                x={Math.floor(slot / 7) * (CELL + GAP)}
                y={(slot % 7) * (CELL + GAP)}
                width={CELL}
                height={CELL}
                rx={2}
              >
                <title>{`${shortDate(day.date)}: ${plural(day.reviews, "review")}`}</title>
              </rect>
            );
          })}
        </svg>
      </div>
      <p className="chart-legend muted" aria-hidden="true">
        Less{" "}
        {[0, 1, 2, 3, 4].map((level) => (
          <svg key={level} width={CELL} height={CELL}>
            <rect className={`heat heat-${String(level)}`} width={CELL} height={CELL} rx={2} />
          </svg>
        ))}{" "}
        More
      </p>
      <TableView
        caption="Reviews per day"
        head={["Day", "Reviews"]}
        rows={days.map((d) => [shortDate(d.date), String(d.reviews)])}
      />
    </Figure>
  );
}

// --- retention curve ---

const PLOT_W = 600;
const PLOT_H = 180;
const PAD = { left: 40, right: 12, top: 10, bottom: 24 };

function percent(rate: number): string {
  return `${String(Math.round(rate * 100))}%`;
}

export function RetentionCurve({ points }: { points: RetentionPoint[] }) {
  const rated = points.filter((p): p is RetentionPoint & { rate: number } => p.rate !== null);
  const first = rated[0];
  const latest = rated[rated.length - 1];
  if (first === undefined || latest === undefined) {
    return <NoData title="Recall rate per week" />;
  }
  const innerW = PLOT_W - PAD.left - PAD.right;
  const innerH = PLOT_H - PAD.top - PAD.bottom;
  const x = (index: number): number =>
    PAD.left + (rated.length === 1 ? innerW / 2 : (index / (rated.length - 1)) * innerW);
  const y = (rate: number): number => PAD.top + (1 - rate) * innerH;
  const path = rated.map((p, i) => `${i === 0 ? "M" : "L"}${String(x(i))},${String(y(p.rate))}`).join(" ");
  return (
    <Figure
      title="Recall rate per week"
      summary={`Recall rate over ${plural(rated.length, "week")}, most recently ${percent(latest.rate)}.`}
    >
      <svg className="line-chart" viewBox={`0 0 ${String(PLOT_W)} ${String(PLOT_H)}`} aria-hidden="true">
        {[0, 0.5, 1].map((tick) => (
          <g key={tick}>
            <line className="grid" x1={PAD.left} x2={PLOT_W - PAD.right} y1={y(tick)} y2={y(tick)} />
            <text className="axis" x={PAD.left - 6} y={y(tick) + 4} textAnchor="end">
              {percent(tick)}
            </text>
          </g>
        ))}
        <path className="line" d={path} fill="none" />
        {rated.map((p, i) => (
          <circle key={p.week_start} className="dot" cx={x(i)} cy={y(p.rate)} r={4}>
            <title>{`Week of ${shortDate(p.week_start)}: ${percent(p.rate)} of ${plural(p.reviews, "review")}`}</title>
          </circle>
        ))}
        <text className="axis" x={PAD.left} y={PLOT_H - 6}>
          {shortDate(first.week_start)}
        </text>
        <text className="axis" x={PLOT_W - PAD.right} y={PLOT_H - 6} textAnchor="end">
          {shortDate(latest.week_start)}
        </text>
      </svg>
      <TableView
        caption="Recall rate per week"
        head={["Week of", "Recall rate"]}
        rows={rated.map((p) => [shortDate(p.week_start), `${percent(p.rate)} of ${plural(p.reviews, "review")}`])}
      />
    </Figure>
  );
}

// --- forecast ---

export function Forecast({ days }: { days: ForecastDay[] }) {
  const first = days[0];
  const last = days[days.length - 1];
  if (first === undefined || last === undefined) {
    return <NoData title="Reviews coming due" />;
  }
  const max = Math.max(1, ...days.map((d) => d.due));
  const total = days.reduce((sum, d) => sum + d.due, 0);
  const innerW = PLOT_W - PAD.left - PAD.right;
  const innerH = PLOT_H - PAD.top - PAD.bottom;
  const slot = innerW / Math.max(1, days.length);
  const barW = Math.max(2, slot - 2);
  return (
    <Figure
      title="Reviews coming due"
      summary={`${plural(total, "review")} due over the next ${plural(days.length, "day")}, ${String(first.due)} today.`}
    >
      <svg className="bar-chart" viewBox={`0 0 ${String(PLOT_W)} ${String(PLOT_H)}`} aria-hidden="true">
        <line className="grid" x1={PAD.left} x2={PLOT_W - PAD.right} y1={PAD.top} y2={PAD.top} />
        <text className="axis" x={PAD.left - 6} y={PAD.top + 4} textAnchor="end">
          {max}
        </text>
        <line className="baseline" x1={PAD.left} x2={PLOT_W - PAD.right} y1={PAD.top + innerH} y2={PAD.top + innerH} />
        <text className="axis" x={PAD.left - 6} y={PAD.top + innerH + 4} textAnchor="end">
          0
        </text>
        {days.map((day, index) => {
          const h = (day.due / max) * innerH;
          return (
            <rect
              key={day.date}
              className="bar"
              x={PAD.left + index * slot + 1}
              y={PAD.top + innerH - h}
              width={barW}
              height={h}
              rx={Math.min(2, barW / 2)}
            >
              <title>{`${shortDate(day.date)}: ${plural(day.due, "review")}`}</title>
            </rect>
          );
        })}
        <text className="axis" x={PAD.left} y={PLOT_H - 6}>
          Today
        </text>
        <text className="axis" x={PLOT_W - PAD.right} y={PLOT_H - 6} textAnchor="end">
          {shortDate(last.date)}
        </text>
      </svg>
      <TableView
        caption="Reviews coming due"
        head={["Day", "Reviews due"]}
        rows={days.map((d) => [shortDate(d.date), String(d.due)])}
      />
    </Figure>
  );
}
