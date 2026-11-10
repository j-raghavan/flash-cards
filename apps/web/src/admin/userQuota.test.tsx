// SPDX-License-Identifier: AGPL-3.0-only
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { UserQuota } from "../api/adminUsage";
import type { AdminUser } from "../api/types";
import { USER, mockApi, renderApp } from "../test/helpers";
import { PAGES_ERROR } from "./QuotasPanel";

const ADMIN = { ...USER, id: "a1", email: "root@example.com", role: "admin" as const };

const account = (id: string, email: string): AdminUser => ({
  id,
  email,
  role: "member",
  email_verified: true,
  suspended: false,
  created_at: "2026-10-01T00:00:00Z",
});
const base = {
  "GET /auth/me": { body: ADMIN },
  "GET /admin/users": { body: [account("a1", "root@example.com"), account("u2", "bob@example.com")] },
};

const quota = (over: Partial<UserQuota> = {}): UserQuota => ({
  user_id: "u2",
  period: "2026-11",
  monthly_pages: 10,
  override: null,
  used_pages: 8,
  ...over,
});

describe("per-user AI quota", () => {
  it("shows a user's use and sets, then clears, an override", async () => {
    const calls = mockApi({
      ...base,
      "GET /admin/users/u2/quota": { body: quota() },
      "PUT /admin/users/u2/quota": (call) => {
        const pages = (call.body as { monthly_pages: number | null }).monthly_pages;
        return { body: quota({ monthly_pages: pages ?? 10, override: pages }) };
      },
    });
    renderApp("/admin");
    const user = userEvent.setup();
    const toggle = await screen.findByRole("button", { name: "AI quota for bob@example.com" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    const input = await screen.findByLabelText("Monthly AI pages for bob@example.com (pages)");
    expect(input).toHaveFocus();
    expect(input).toHaveValue("");
    expect(
      screen.getByText(/8 pages used of 10 pages in November 2026\. The instance default applies\./),
    ).toBeInTheDocument();
    expect(input).toHaveAccessibleDescription(/8 pages used of 10 pages.*Leave empty to use the instance default\./);
    expect(screen.queryByRole("button", { name: "Use the default" })).not.toBeInTheDocument();

    await user.type(input, "50");
    await user.click(screen.getByRole("button", { name: "Save quota" }));
    expect(await screen.findByText("Saved: 50 pages a month.")).toBeInTheDocument();
    expect(screen.getByText(/This is an override of the instance default\./)).toBeInTheDocument();
    expect(input).toHaveValue("50");
    expect(input).toHaveFocus();

    await user.click(screen.getByRole("button", { name: "Use the default" }));
    expect(await screen.findByText("Override cleared: the instance default of 10 pages applies.")).toBeInTheDocument();
    expect(input).toHaveValue("");
    expect(calls.filter((c) => c.method === "PUT").map((c) => c.body)).toEqual([
      { monthly_pages: 50 },
      { monthly_pages: null },
    ]);

    // Saving an empty field also clears the override.
    await user.click(screen.getByRole("button", { name: "Save quota" }));
    await waitFor(() => {
      expect(calls.filter((c) => c.method === "PUT")).toHaveLength(3);
    });
    expect(calls.filter((c) => c.method === "PUT")[2]?.body).toEqual({ monthly_pages: null });

    await user.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByLabelText(/Monthly AI pages/)).not.toBeInTheDocument();
    expect(toggle).toHaveFocus();
    await user.click(toggle);
    await screen.findByLabelText(/Monthly AI pages/);
    await user.click(toggle);
    expect(screen.queryByLabelText(/Monthly AI pages/)).not.toBeInTheDocument();
  });

  it("refuses a malformed limit and asks before a limit below this month's use", async () => {
    const calls = mockApi({
      ...base,
      "GET /admin/users/a1/quota": { body: quota({ user_id: "a1", override: 3, monthly_pages: 3, used_pages: 2 }) },
      "PUT /admin/users/a1/quota": (call) => {
        const pages = (call.body as { monthly_pages: number }).monthly_pages;
        return { body: quota({ user_id: "a1", override: pages, monthly_pages: pages, used_pages: 2 }) };
      },
    });
    renderApp("/admin");
    const user = userEvent.setup();
    // Admins can set their own limit too.
    await user.click(await screen.findByRole("button", { name: "AI quota for root@example.com" }));
    const input = await screen.findByLabelText("Monthly AI pages for root@example.com (pages)");
    expect(screen.getByText(/2 pages used of 3 pages/)).toBeInTheDocument();
    for (const bad of ["100001", "12a", "1.5"]) {
      await user.clear(input);
      await user.type(input, bad);
      await user.click(screen.getByRole("button", { name: "Save quota" }));
      expect(input).toHaveAttribute("aria-invalid", "true");
      expect(input).toHaveAccessibleDescription(expect.stringContaining(PAGES_ERROR));
      expect(input).toHaveFocus();
    }

    // A limit equal to the pages already used stops generation too.
    await user.clear(input);
    await user.type(input, "2");
    await user.click(screen.getByRole("button", { name: "Save quota" }));
    const confirm = screen.getByRole("alertdialog", { name: "Confirm the quota" });
    expect(confirm).toHaveTextContent("root@example.com has already used 2 pages this month. A limit of 2 pages stops");
    expect(within(confirm).getByRole("button", { name: "Cancel" })).toHaveFocus();
    await user.click(within(confirm).getByRole("button", { name: "Cancel" }));
    expect(input).toHaveFocus();
    // Editing the field while the confirmation is open withdraws it.
    await user.click(screen.getByRole("button", { name: "Save quota" }));
    await user.clear(input);
    await user.type(input, "1");
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
    await user.click(screen.getByRole("button", { name: "Save quota" }));
    expect(screen.getByRole("alertdialog")).toHaveTextContent("A limit of 1 page stops");
    await user.click(screen.getByRole("button", { name: "Set the limit" }));
    expect(await screen.findByText("Saved: 1 page a month.")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ monthly_pages: 1 });
    expect(input).toHaveFocus();
  });

  it("shows a quota that cannot load or save", async () => {
    mockApi({
      ...base,
      "GET /admin/users/u2/quota": { status: 404, body: { detail: "No such user." } },
      "GET /admin/users/a1/quota": { body: quota({ user_id: "a1" }) },
      "PUT /admin/users/a1/quota": { status: 500 },
    });
    renderApp("/admin");
    const user = userEvent.setup();
    const toggle = await screen.findByRole("button", { name: "AI quota for bob@example.com" });
    await user.click(toggle);
    expect(await screen.findByRole("alert")).toHaveTextContent("No such user.");
    await user.click(screen.getByRole("button", { name: "Close" }));
    expect(toggle).toHaveFocus();

    await user.click(screen.getByRole("button", { name: "AI quota for root@example.com" }));
    await user.type(await screen.findByLabelText(/Monthly AI pages/), "20");
    await user.click(screen.getByRole("button", { name: "Save quota" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong on the server.");
    expect(screen.getByLabelText(/Monthly AI pages/)).toHaveFocus();
  });
});
