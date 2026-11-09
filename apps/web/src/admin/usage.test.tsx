// SPDX-License-Identifier: AGPL-3.0-only
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { adminUsage, currentPeriod, formatUsd, periodLabel, shiftPeriod, type UsageReport } from "../api/adminUsage";
import type { AdminUser } from "../api/types";
import { USER, mockApi, renderApp } from "../test/helpers";
import { CHART_BARS } from "./UsagePanel";

const ADMIN = { ...USER, id: "a1", email: "root@example.com", role: "admin" as const };
const asAdmin = { "GET /auth/me": { body: ADMIN } };

const account = (id: string, email: string): AdminUser => ({
  id,
  email,
  role: "member",
  email_verified: true,
  suspended: false,
  created_at: "2026-10-01T00:00:00Z",
});
const USERS = [account("a1", "root@example.com"), account("u2", "bob@example.com")];

const usage = (userId: string, cost: number, tokens = 1000) => ({
  user_id: userId,
  calls: 2,
  input_tokens: tokens,
  output_tokens: tokens / 2,
  cost_micro_usd: cost,
});

const REPORT: UsageReport = {
  period: "2026-11",
  cost_micro_usd: 12_504_200,
  spend_cap_micro_usd: 50_000_000,
  by_user: [usage("u2", 2_500_000), usage("a1", 10_004_200), usage("gone", 0)],
  by_provider: [
    { provider: "anthropic", model: "claude-sonnet", calls: 5, input_tokens: 3000, output_tokens: 1500, cost_micro_usd: 12_504_200 },
  ],
};

async function openUsage(): Promise<ReturnType<typeof userEvent.setup>> {
  const user = userEvent.setup();
  await user.click(await screen.findByRole("tab", { name: "Usage" }));
  return user;
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-11-08T12:00:00Z"));
});

afterEach(() => {
  vi.useRealTimers();
});

describe("usage helpers", () => {
  it("formats micro-USD as dollars, keeping digits under a cent", () => {
    expect(formatUsd(12_500_000)).toBe("$12.50");
    expect(formatUsd(0)).toBe("$0.00");
    expect(formatUsd(4200)).toBe("$0.0042");
  });

  it("names and shifts billing months in UTC", () => {
    expect(currentPeriod(new Date("2026-12-31T23:30:00-05:00"))).toBe("2027-01");
    expect(shiftPeriod("2026-01", -1)).toBe("2025-12");
    expect(shiftPeriod("2026-12", 1)).toBe("2027-01");
    expect(periodLabel("2026-11")).toMatch(/November 2026/);
  });

  it("asks for the current month when no period is given", async () => {
    const calls = mockApi({ "GET /admin/usage": { body: REPORT } });
    await adminUsage.usage(null);
    expect(calls[0]?.path).toBe("/admin/usage");
  });
});

describe("usage report", () => {
  it("shows the month's totals, per user and per model, with emails and a table for each chart", async () => {
    mockApi({ ...asAdmin, "GET /admin/users": { body: USERS }, "GET /admin/usage?period=2026-11": { body: REPORT } });
    renderApp("/admin");
    await openUsage();
    expect(await screen.findByRole("heading", { name: /November 2026/ })).toBeInTheDocument();
    const totals = (await screen.findByText("Estimated cost")).closest("dl") as HTMLElement;
    expect(within(totals).getByText("$12.50")).toBeInTheDocument();
    expect(within(totals).getByText("$50.00")).toBeInTheDocument();
    expect(within(totals).getByText("1,500")).toBeInTheDocument();
    expect(screen.queryByText("Prices are not configured.")).not.toBeInTheDocument();

    const byUser = screen.getByRole("table", { name: "Usage by user" });
    await waitFor(() => {
      expect(within(byUser).getAllByRole("rowheader").map((c) => c.textContent)).toEqual([
        "root@example.com",
        "bob@example.com",
        "Deleted account gone",
      ]);
    });
    expect(within(byUser).getByText("$2.50")).toBeInTheDocument();
    const byModel = screen.getByRole("table", { name: "Usage by provider and model" });
    expect(within(byModel).getByText("anthropic claude-sonnet")).toBeInTheDocument();
    expect(within(byModel).getByText("3,000")).toBeInTheDocument();
    // The screen reader summary and the tooltips carry the same numbers.
    expect(screen.getByText(/3 entries by cost; the largest is root@example.com with \$10.00/)).toBeInTheDocument();
    expect(document.querySelector("svg.bar-chart rect title")?.textContent).toBe(
      "root@example.com: 2 calls, 1,000 input and 500 output tokens, $10.00",
    );
  });

  it("says when prices are not configured and ranks by tokens instead", async () => {
    const unpriced: UsageReport = {
      period: "2026-11",
      cost_micro_usd: 0,
      spend_cap_micro_usd: null,
      by_user: [usage("u2", 0, 4000)],
      by_provider: [{ provider: "openai", model: "local", calls: 2, input_tokens: 4000, output_tokens: 2000, cost_micro_usd: 0 }],
    };
    mockApi({ ...asAdmin, "GET /admin/users": { status: 500 }, "GET /admin/usage": { body: unpriced } });
    renderApp("/admin");
    await openUsage();
    expect(await screen.findByText("Prices are not configured.")).toBeInTheDocument();
    expect(screen.getByText("No cap")).toBeInTheDocument();
    // Without the user list, ids stand in for emails.
    expect(screen.getByText(/1 entry by tokens; the largest is u2 with 6,000 tokens/)).toBeInTheDocument();
  });

  it("draws at most the top bars, the table keeps every row", async () => {
    const many: UsageReport = {
      ...REPORT,
      by_user: Array.from({ length: CHART_BARS + 2 }, (_, n) => usage(`user-with-a-very-long-identifier-${String(n)}`, Math.floor(n / 2) + 1)),
      by_provider: [],
    };
    mockApi({ ...asAdmin, "GET /admin/users": { body: [] }, "GET /admin/usage": { body: many } });
    renderApp("/admin");
    await openUsage();
    const table = await screen.findByRole("table", { name: "Usage by user" });
    expect(within(table).getAllByRole("row")).toHaveLength(CHART_BARS + 3);
    const chart = document.querySelector("svg.bar-chart") as SVGElement;
    expect(chart.querySelectorAll("rect")).toHaveLength(CHART_BARS);
    expect(chart.textContent).toContain("…");
    expect(screen.getByText("No AI use this month.")).toBeInTheDocument();
  });

  it("moves between months, refuses a malformed one and never goes past this month", async () => {
    const calls = mockApi({
      ...asAdmin,
      "GET /admin/users": { body: USERS },
      "GET /admin/usage": (call) => ({
        body: { ...REPORT, period: new URLSearchParams(call.path.split("?")[1]).get("period"), by_user: [], by_provider: [], cost_micro_usd: 0 },
      }),
    });
    renderApp("/admin");
    const user = await openUsage();
    expect(await screen.findByText(/No AI use in November 2026/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next month" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Previous month" }));
    expect(await screen.findByText(/No AI use in October 2026/)).toBeInTheDocument();
    expect(screen.getByLabelText("Month (YYYY-MM)")).toHaveValue("2026-10");
    await user.click(screen.getByRole("button", { name: "Next month" }));
    expect(await screen.findByText(/No AI use in November 2026/)).toBeInTheDocument();

    const month = screen.getByLabelText("Month (YYYY-MM)");
    fireEvent.change(month, { target: { value: "2026-03" } });
    expect(await screen.findByText(/No AI use in March 2026/)).toBeInTheDocument();
    // A browser without a month picker shows a text box: a partial value waits.
    month.setAttribute("type", "text");
    fireEvent.change(month, { target: { value: "2026-1" } });
    expect(screen.queryByText(/Enter a month/)).not.toBeInTheDocument();
    fireEvent.blur(month);
    expect(screen.getByText("Enter a month as YYYY-MM, such as 2026-11.")).toHaveAttribute("id", "usage-period-error");
    expect(month).toHaveAttribute("aria-invalid", "true");
    expect(month).toHaveAttribute("aria-describedby", "usage-period-error");
    expect(calls.filter((c) => c.path.startsWith("/admin/usage")).map((c) => c.path)).toEqual([
      "/admin/usage?period=2026-11",
      "/admin/usage?period=2026-10",
      "/admin/usage?period=2026-11",
      "/admin/usage?period=2026-03",
    ]);
  });

  it("reports a report that cannot load", async () => {
    mockApi({ ...asAdmin, "GET /admin/users": { body: USERS }, "GET /admin/usage": { status: 500 } });
    renderApp("/admin");
    await openUsage();
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong");
  });
});
