// SPDX-License-Identifier: AGPL-3.0-only
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { ProgressStats, Today } from "../api/studyProgress";
import { USER, mockApi, renderApp } from "../test/helpers";
import { Forecast, Heatmap, RetentionCurve, heatLevel } from "./charts";
import { deviceTimeZone, timeZones } from "./ProgressPage";

const me = { "GET /auth/me": { body: USER } };

const TODAY: Today = {
  date: "2026-11-08",
  time_zone: "UTC",
  reviews: 12,
  daily_goal: 20,
  goal_met: false,
  just_completed: false,
  streak: 4,
};

const STATS: ProgressStats = {
  time_zone: "UTC",
  deck_id: null,
  heatmap: [
    { date: "2026-11-06", reviews: 0 },
    { date: "2026-11-07", reviews: 8 },
    { date: "2026-11-08", reviews: 1 },
  ],
  retention: [
    { week_start: "2026-10-26", reviews: 40, recalled: 34, rate: 0.85 },
    { week_start: "2026-11-02", reviews: 0, recalled: 0, rate: null },
    { week_start: "2026-11-09", reviews: 10, recalled: 9, rate: 0.9 },
  ],
  forecast: [
    { date: "2026-11-08", due: 5 },
    { date: "2026-11-09", due: 1 },
  ],
};

describe("charts", () => {
  it("grades heat by share of the busiest day", () => {
    expect(heatLevel(0, 10)).toBe(0);
    expect(heatLevel(3, 0)).toBe(0);
    expect(heatLevel(1, 10)).toBe(1);
    expect(heatLevel(10, 10)).toBe(4);
  });

  it("summarizes each chart for screen readers and offers the numbers as a table", () => {
    render(
      <>
        <Heatmap days={STATS.heatmap} />
        <RetentionCurve points={STATS.retention} />
        <Forecast days={STATS.forecast} />
      </>,
    );
    expect(screen.getByText("9 reviews on 2 days of the last 3 days.")).toBeInTheDocument();
    expect(screen.getByText("Recall rate over 2 weeks, most recently 90%.")).toBeInTheDocument();
    expect(screen.getByText("6 reviews due over the next 2 days, 5 today.")).toBeInTheDocument();
    const tables = screen.getAllByRole("table", { hidden: true });
    expect(tables).toHaveLength(3);
    expect(within(tables[1] as HTMLElement).getAllByRole("row", { hidden: true })).toHaveLength(3);
    expect(document.querySelectorAll("rect.heat-4")).toHaveLength(2); // the busiest day and its legend swatch
    expect(document.querySelector("circle title")?.textContent).toMatch(/85% of 40 reviews/);
  });

  it("draws a single week in the middle and says when a range holds nothing", () => {
    const { container } = render(
      <RetentionCurve points={[{ week_start: "2026-11-02", reviews: 1, recalled: 1, rate: 1 }]} />,
    );
    expect(container.querySelector("circle")?.getAttribute("cx")).toBe("314");
    expect(screen.getByText("Recall rate over 1 week, most recently 100%.")).toBeInTheDocument();
    render(
      <>
        <Heatmap days={[]} />
        <RetentionCurve points={[]} />
        <Forecast days={[]} />
      </>,
    );
    expect(screen.getAllByText("No data in this range yet.", { selector: "p.empty" })).toHaveLength(3);
  });
});

describe("time zones", () => {
  it("lists the browser's zones with the saved one kept", () => {
    expect(timeZones("Europe/Paris")).toContain("Europe/Paris");
    expect(timeZones("Mars/Olympus")[0]).toBe("Mars/Olympus");
    expect(deviceTimeZone()).toBe(Intl.DateTimeFormat().resolvedOptions().timeZone);
  });
});

describe("progress page", () => {
  it("shows today's goal and streak, statistics, and filters them by deck and range", async () => {
    const calls = mockApi({
      ...me,
      "GET /progress/today": { body: TODAY },
      "GET /progress/settings": { body: { daily_goal: 20, time_zone: "UTC" } },
      "GET /decks": {
        body: [{ id: "d1", name: "Biology", parent_id: null, created_at: "2026-01-01T00:00:00Z", owned: true }],
      },
      "GET /progress/stats": { body: STATS },
    });
    renderApp("/progress");
    const user = userEvent.setup();
    expect(await screen.findByRole("heading", { name: "Progress" })).toBeInTheDocument();
    expect(await screen.findByText("Streak: 4 days in a row.")).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "12 of 20 cards" })).toHaveAttribute("value", "12");
    expect(screen.queryByText("Daily goal reached. Well done.")).not.toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Reviews per day" })).toBeInTheDocument();
    await waitFor(() => {
      expect(document.title).toBe("Progress · Flash-Cards");
    });
    await user.selectOptions(await screen.findByLabelText("Deck"), "d1");
    await user.selectOptions(screen.getByLabelText("Range"), "365");
    await screen.findByRole("heading", { name: "Reviews coming due" });
    expect(calls.filter((c) => c.path.startsWith("/progress/stats")).map((c) => c.path)).toEqual([
      "/progress/stats?days=90&forecast_days=30",
      "/progress/stats?days=90&forecast_days=30&deck_id=d1",
      "/progress/stats?days=365&forecast_days=30&deck_id=d1",
    ]);
  });

  it("celebrates a met goal and invites a first streak", async () => {
    mockApi({
      ...me,
      "GET /progress/today": { body: { ...TODAY, reviews: 25, goal_met: true, streak: 1 } },
      "GET /progress/settings": { status: 500 },
      "GET /decks": { body: [] },
      "GET /progress/stats": { body: STATS },
    });
    const { unmount } = renderApp("/progress");
    expect(await screen.findByText("Daily goal reached. Well done.")).toBeInTheDocument();
    expect(screen.getByText("Streak: 1 day in a row.")).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toHaveAttribute("value", "20");
    unmount();
    mockApi({
      ...me,
      "GET /progress/today": { body: { ...TODAY, streak: 0 } },
      "GET /progress/settings": { status: 500 },
      "GET /decks": { body: [] },
      "GET /progress/stats": { status: 500 },
    });
    renderApp("/progress");
    expect(await screen.findByText("Meet today's goal to start a streak.")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getAllByRole("alert")).toHaveLength(2);
    });
  });

  it("saves the daily goal and time zone, then reloads today's numbers", async () => {
    vi.spyOn(Intl.DateTimeFormat.prototype, "resolvedOptions").mockReturnValue({
      ...new Intl.DateTimeFormat().resolvedOptions(),
      timeZone: "Europe/Paris",
    });
    let goal = 20;
    const calls = mockApi({
      ...me,
      "GET /progress/today": () => ({ body: { ...TODAY, daily_goal: goal } }),
      "GET /progress/settings": { body: { daily_goal: 20, time_zone: "UTC" } },
      "PATCH /progress/settings": (call) => {
        goal = (call.body as { daily_goal: number }).daily_goal;
        return { body: call.body };
      },
      "GET /decks": { body: [] },
      "GET /progress/stats": { body: STATS },
    });
    renderApp("/progress");
    const user = userEvent.setup();
    const input = await screen.findByLabelText("Daily goal (cards)");
    const save = screen.getByRole("button", { name: "Save" });
    await user.clear(input);
    await user.type(input, "0");
    expect(save).toBeDisabled();
    await user.clear(input);
    await user.type(input, "30");
    await user.click(screen.getByRole("button", { name: "Use this device's zone (Europe/Paris)" }));
    expect(screen.getByLabelText("Time zone (when a day starts)")).toHaveValue("Europe/Paris");
    expect(screen.queryByRole("button", { name: /Use this device's zone/ })).not.toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Time zone (when a day starts)"), "UTC");
    await user.selectOptions(screen.getByLabelText("Time zone (when a day starts)"), "Europe/Paris");
    await user.click(save);
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    expect(await screen.findByRole("progressbar", { name: "12 of 30 cards" })).toBeInTheDocument();
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ daily_goal: 30, time_zone: "Europe/Paris" });
  });

  it("reports settings the server refuses and today that cannot load", async () => {
    mockApi({
      ...me,
      "GET /progress/today": { status: 500 },
      "GET /progress/settings": { body: { daily_goal: 20, time_zone: "UTC" } },
      "PATCH /progress/settings": { status: 422, body: { detail: [{ msg: "Value error, unknown time zone" }] } },
      "GET /decks": { body: [] },
      "GET /progress/stats": { body: STATS },
    });
    renderApp("/progress");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Save" }));
    await waitFor(() => {
      expect(screen.getAllByRole("alert").map((a) => a.textContent)).toContain("unknown time zone");
    });
  });
});
