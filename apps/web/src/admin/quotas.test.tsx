// SPDX-License-Identifier: AGPL-3.0-only
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { parsePages, parseUsd, usdInput, type UsageReport } from "../api/adminUsage";
import { USER, mockApi, renderApp } from "../test/helpers";
import { CAP_ERROR, PAGES_ERROR } from "./QuotasPanel";

const ADMIN = { ...USER, id: "a1", email: "root@example.com", role: "admin" as const };
const asAdmin = { "GET /auth/me": { body: ADMIN }, "GET /admin/users": { body: [] } };

const spending = (cost: number, tokens = 1000): UsageReport => ({
  period: "2026-11",
  cost_micro_usd: cost,
  spend_cap_micro_usd: null,
  by_user: [],
  by_provider: [
    { provider: "anthropic", model: "m", calls: 1, input_tokens: tokens, output_tokens: 0, cost_micro_usd: cost },
  ],
});

async function openQuotas(): Promise<ReturnType<typeof userEvent.setup>> {
  const user = userEvent.setup();
  await user.click(await screen.findByRole("tab", { name: "Quotas" }));
  return user;
}

describe("quota helpers", () => {
  it("parses dollars exactly, as micro-USD", () => {
    expect(parseUsd("25")).toBe(25_000_000);
    expect(parseUsd(" $12.50 ")).toBe(12_500_000);
    expect(parseUsd("0.000001")).toBe(1);
    expect(parseUsd("0.29")).toBe(290_000);
    expect(parseUsd("3.")).toBe(3_000_000);
    expect(parseUsd("1,000")).toBeNull();
    expect(parseUsd("-1")).toBeNull();
    expect(parseUsd("0.0000001")).toBeNull();
    expect(parseUsd("")).toBeNull();
  });

  it("writes micro-USD back as plain dollars", () => {
    expect(usdInput(12_500_000)).toBe("12.5");
    expect(usdInput(25_000_000)).toBe("25");
    expect(usdInput(1)).toBe("0.000001");
  });

  it("accepts whole page counts within the server's bounds", () => {
    expect(parsePages(" 40 ")).toBe(40);
    expect(parsePages("0")).toBe(0);
    expect(parsePages("100000")).toBe(100_000);
    expect(parsePages("100001")).toBeNull();
    expect(parsePages("1.5")).toBeNull();
    expect(parsePages("")).toBeNull();
  });
});

describe("instance quotas", () => {
  it("shows the settings and this month's spend, and saves new values", async () => {
    const calls = mockApi({
      ...asAdmin,
      "GET /admin/quotas": { body: { default_monthly_pages: 10, monthly_spend_cap_micro_usd: 50_000_000 } },
      "GET /admin/usage": { body: spending(12_500_000) },
      "PUT /admin/quotas": (call) => ({ body: call.body }),
      "GET /admin/audit": { body: [] },
    });
    renderApp("/admin");
    const user = await openQuotas();
    const pages = await screen.findByLabelText("Default monthly limit per user (pages)");
    const cap = screen.getByLabelText("Monthly spend cap (US dollars)");
    expect(pages).toHaveValue("10");
    expect(cap).toHaveValue("50");
    expect(pages).toHaveAccessibleDescription(/AI pages each user may generate/);
    expect(await screen.findByText("$12.50 spent of the $50.00 cap.")).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "$12.50 spent of the $50.00 cap." })).toHaveAttribute(
      "value",
      "12500000",
    );

    await user.clear(pages);
    await user.type(pages, "40");
    await user.clear(cap);
    await user.type(cap, "75.25");
    await user.click(screen.getByRole("button", { name: "Save quotas" }));
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "PUT")?.body).toEqual({
      default_monthly_pages: 40,
      monthly_spend_cap_micro_usd: 75_250_000,
    });
    expect(await screen.findByText("$12.50 spent of the $75.25 cap.")).toBeInTheDocument();

    // Removing the cap sends null.
    await user.clear(cap);
    await user.click(screen.getByRole("button", { name: "Save quotas" }));
    await waitFor(() => {
      expect(calls.filter((c) => c.method === "PUT")[1]?.body).toEqual({
        default_monthly_pages: 40,
        monthly_spend_cap_micro_usd: null,
      });
    });
    expect(await screen.findByText(/There is no spend cap/)).toBeInTheDocument();

    // The audit log loads afresh when opened after a change.
    await user.click(screen.getByRole("tab", { name: "Audit log" }));
    expect(await screen.findByText("No events yet.")).toBeInTheDocument();
    expect(calls.some((c) => c.path.startsWith("/admin/audit"))).toBe(true);
  });

  it("shows no default limit as an empty field, and clearing it saves no limit", async () => {
    const calls = mockApi({
      ...asAdmin,
      "GET /admin/quotas": { body: { default_monthly_pages: null, monthly_spend_cap_micro_usd: null } },
      "GET /admin/usage": { body: spending(0, 0) },
      "PUT /admin/quotas": (call) => ({ body: call.body }),
      "GET /admin/audit": { body: [] },
    });
    renderApp("/admin");
    const user = await openQuotas();
    const pages = await screen.findByLabelText("Default monthly limit per user (pages)");
    expect(pages).toHaveValue(""); // empty: members have no limit
    expect(pages).toHaveAccessibleDescription(/Leave empty for\s+no limit/);

    // A number meters members.
    await user.type(pages, "25");
    await user.click(screen.getByRole("button", { name: "Save quotas" }));
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "PUT")?.body).toEqual({
      default_monthly_pages: 25,
      monthly_spend_cap_micro_usd: null,
    });

    // Clearing it again removes the limit (sends null).
    await user.clear(pages);
    await user.click(screen.getByRole("button", { name: "Save quotas" }));
    await waitFor(() => {
      expect(calls.filter((c) => c.method === "PUT")[1]?.body).toEqual({
        default_monthly_pages: null,
        monthly_spend_cap_micro_usd: null,
      });
    });
  });

  it("checks the fields like the server, focusing the first wrong one", async () => {
    const calls = mockApi({
      ...asAdmin,
      "GET /admin/quotas": { body: { default_monthly_pages: 10, monthly_spend_cap_micro_usd: null } },
      "GET /admin/usage": { body: spending(0, 0) },
    });
    renderApp("/admin");
    const user = await openQuotas();
    const pages = await screen.findByLabelText("Default monthly limit per user (pages)");
    const cap = screen.getByLabelText("Monthly spend cap (US dollars)");
    expect(cap).toHaveValue("");
    await user.clear(pages);
    // Non-numeric text is an error, never silently read as "no limit".
    await user.type(pages, "12a");
    await user.click(screen.getByRole("button", { name: "Save quotas" }));
    expect(pages).toHaveAttribute("aria-invalid", "true");
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
    await user.clear(pages);
    await user.type(pages, "100001");
    await user.type(cap, "10000000.01");
    await user.click(screen.getByRole("button", { name: "Save quotas" }));
    expect(pages).toHaveFocus();
    expect(pages).toHaveAttribute("aria-invalid", "true");
    expect(pages).toHaveAccessibleDescription(expect.stringContaining(PAGES_ERROR));
    expect(cap).toHaveAccessibleDescription(expect.stringContaining(CAP_ERROR));
    await user.clear(pages);
    await user.type(pages, "5");
    await user.click(screen.getByRole("button", { name: "Save quotas" }));
    expect(cap).toHaveFocus();
    expect(pages).toHaveAttribute("aria-invalid", "false");
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
    expect(screen.queryByText(/Prices are not configured/)).not.toBeInTheDocument();
  });

  it("asks before setting a cap below this month's spend", async () => {
    const calls = mockApi({
      ...asAdmin,
      "GET /admin/quotas": { body: { default_monthly_pages: 10, monthly_spend_cap_micro_usd: 50_000_000 } },
      "GET /admin/usage": { body: spending(12_500_000) },
      "PUT /admin/quotas": (call) => ({ body: call.body }),
    });
    renderApp("/admin");
    const user = await openQuotas();
    const cap = await screen.findByLabelText("Monthly spend cap (US dollars)");
    await screen.findByText(/spent of the/);
    await user.clear(cap);
    await user.type(cap, "10");
    await user.click(screen.getByRole("button", { name: "Save quotas" }));
    const confirm = screen.getByRole("alertdialog", { name: "Confirm the spend cap" });
    expect(confirm).toHaveTextContent("This month has already spent $12.50. A cap of $10.00 refuses all new AI work");
    expect(within(confirm).getByRole("button", { name: "Cancel" })).toHaveFocus();
    await user.click(within(confirm).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(cap).toHaveFocus();
    expect(calls.some((c) => c.method === "PUT")).toBe(false);

    // A cap equal to this month's spend is reached at once, so it asks too.
    await user.clear(cap);
    await user.type(cap, "12.5");
    await user.click(screen.getByRole("button", { name: "Save quotas" }));
    expect(screen.getByRole("alertdialog")).toHaveTextContent("A cap of $12.50 refuses all new AI work");
    // Editing a field while the confirmation is open withdraws it.
    await user.clear(cap);
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    await user.type(cap, "10");

    await user.click(screen.getByRole("button", { name: "Save quotas" }));
    await user.click(screen.getByRole("button", { name: "Set the cap" }));
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    expect(cap).toHaveFocus();
    expect(calls.find((c) => c.method === "PUT")?.body).toEqual({
      default_monthly_pages: 10,
      monthly_spend_cap_micro_usd: 10_000_000,
    });
    expect(await screen.findByText(/The cap is reached/)).toBeInTheDocument();

    // Saving the same cap again needs no second confirmation.
    await user.click(screen.getByRole("button", { name: "Save quotas" }));
    await waitFor(() => {
      expect(calls.filter((c) => c.method === "PUT")).toHaveLength(2);
    });
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("says when prices are missing, and shows refusals and loading failures", async () => {
    mockApi({
      ...asAdmin,
      "GET /admin/quotas": { body: { default_monthly_pages: 10, monthly_spend_cap_micro_usd: 5_000_000 } },
      "GET /admin/usage": { body: spending(0) },
      "PUT /admin/quotas": { status: 422, body: { detail: [{ msg: "Input should be less than or equal to 100000" }] } },
    });
    const { unmount } = renderApp("/admin");
    const user = await openQuotas();
    expect(await screen.findByText(/Prices are not configured/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save quotas" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Input should be less than or equal to 100000");
    unmount();

    mockApi({
      ...asAdmin,
      "GET /admin/quotas": { body: { default_monthly_pages: 10, monthly_spend_cap_micro_usd: null } },
      "GET /admin/usage": { status: 500 },
    });
    const second = renderApp("/admin");
    const again = await openQuotas();
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong");
    // Without this month's spend a new cap cannot be compared with it, so it asks.
    await again.type(screen.getByLabelText("Monthly spend cap (US dollars)"), "5");
    await again.click(screen.getByRole("button", { name: "Save quotas" }));
    expect(screen.getByRole("alertdialog")).toHaveTextContent("This month's spend could not be loaded.");
    second.unmount();

    mockApi({ ...asAdmin, "GET /admin/quotas": { status: 403, body: { detail: "Admins only." } } });
    renderApp("/admin");
    await openQuotas();
    expect(await screen.findByRole("alert")).toHaveTextContent("Admins only.");
  });
});
