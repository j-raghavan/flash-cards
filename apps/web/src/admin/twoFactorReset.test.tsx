// SPDX-License-Identifier: AGPL-3.0-only
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { AdminUser } from "../api/types";
import { USER, mockApi, renderApp } from "../test/helpers";

const ADMIN = { ...USER, id: "a1", email: "root@example.com", role: "admin" as const };

const account = (id: string, email: string, over: Partial<AdminUser> = {}): AdminUser => ({
  id,
  email,
  role: "member",
  email_verified: true,
  suspended: false,
  created_at: "2026-10-01T00:00:00Z",
  ...over,
});

describe("admin two-step sign-in reset", () => {
  it("shows who has two-step sign-in and resets it, keeping the flag across other changes", async () => {
    const calls = mockApi({
      "GET /auth/me": { body: ADMIN },
      "GET /admin/users": {
        body: [
          account("a1", "root@example.com", { role: "admin", two_factor: true }),
          account("u2", "bob@example.com", { two_factor: true }),
          account("u3", "eve@example.com", { two_factor: false }),
        ],
      },
      "POST /admin/users/u2/unlock": { body: account("u2", "bob@example.com", { two_factor: null }) },
      "POST /admin/users/u2/two-factor/reset": { body: account("u2", "bob@example.com", { two_factor: false }) },
      "POST /admin/users/u3/two-factor/reset": { status: 404, body: { detail: "No such user." } },
    });
    renderApp("/admin");
    const user = userEvent.setup();
    const own = (await screen.findByText("root@example.com")).closest("tr") as HTMLElement;
    expect(within(own).queryByRole("button", { name: /Reset two-step/ })).not.toBeInTheDocument();
    const eve = screen.getByText("eve@example.com").closest("tr") as HTMLElement;
    expect(within(eve).queryByRole("button", { name: /Reset two-step/ })).not.toBeInTheDocument();
    const bob = screen.getByText("bob@example.com").closest("tr") as HTMLElement;
    expect(within(bob).getByText("Active, two-step sign-in on")).toBeInTheDocument();

    await user.click(within(bob).getByRole("button", { name: "Unlock sign-in for bob@example.com" }));
    expect(await within(bob).findByText("Sign-in unlocked.")).toBeInTheDocument();
    expect(within(bob).getByText("Active, two-step sign-in on")).toBeInTheDocument();

    await user.click(within(bob).getByRole("button", { name: "Reset two-step sign-in for bob@example.com" }));
    await user.click(within(bob).getByRole("button", { name: "Reset two-step sign-in" }));
    expect(await within(bob).findByText("Two-step sign-in reset.")).toBeInTheDocument();
    expect(within(bob).getByText("Active")).toBeInTheDocument();
    expect(calls.some((c) => c.path === "/admin/users/u2/two-factor/reset")).toBe(true);
  });

  it("reports a failed reset", async () => {
    mockApi({
      "GET /auth/me": { body: ADMIN },
      "GET /admin/users": { body: [account("u2", "bob@example.com", { two_factor: true })] },
      "POST /admin/users/u2/two-factor/reset": { status: 404, body: { detail: "No such user." } },
    });
    renderApp("/admin");
    const user = userEvent.setup();
    const bob = (await screen.findByText("bob@example.com")).closest("tr") as HTMLElement;
    await user.click(within(bob).getByRole("button", { name: "Reset two-step sign-in for bob@example.com" }));
    await user.click(within(bob).getByRole("button", { name: "Reset two-step sign-in" }));
    expect(await within(bob).findByRole("alert")).toHaveTextContent("No such user.");
  });
});
