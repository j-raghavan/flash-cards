// SPDX-License-Identifier: AGPL-3.0-only
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { AdminUser, AuditEvent, Invite } from "../api/types";
import { USER, mockApi, renderApp } from "../test/helpers";
import { AUDIT_PAGE, inviteUrl } from "./AdminPage";

const ADMIN = { ...USER, id: "a1", email: "root@example.com", role: "admin" as const };
const asAdmin = { "GET /auth/me": { body: ADMIN } };

const account = (id: string, email: string, over: Partial<AdminUser> = {}): AdminUser => ({
  id,
  email,
  role: "member",
  email_verified: true,
  suspended: false,
  created_at: "2026-10-01T00:00:00Z",
  ...over,
});

const USERS = [
  account("a1", "root@example.com", { role: "admin" }),
  account("u2", "bob@example.com", { email_verified: false }),
];

const invite = (id: string, over: Partial<Invite> = {}): Invite => ({
  id,
  role: "member",
  email: null,
  status: "active",
  created_at: "2026-11-01T00:00:00Z",
  expires_at: "2026-11-08T00:00:00Z",
  ...over,
});

const event = (n: number): AuditEvent => ({
  id: `e${String(n)}`,
  action: n === 0 ? "user.suspend" : `event.${String(n)}`,
  created_at: "2026-11-08T10:00:00Z",
  actor_id: n === 0 ? "a1" : null,
  target_id: n === 0 ? "u2" : n === 1 ? "gone" : null,
  detail: n === 0 ? { reason: "<b>spam</b>" } : {},
});

describe("admin console", () => {
  it("is hidden from members, in the navigation and on the page", async () => {
    mockApi({ "GET /auth/me": { body: USER }, "GET /decks": { body: [] } });
    const { unmount } = renderApp("/");
    await screen.findByRole("heading", { name: "Decks" });
    expect(screen.queryByRole("link", { name: "Admin" })).not.toBeInTheDocument();
    unmount();
    const calls = mockApi({ "GET /auth/me": { body: USER } });
    renderApp("/admin");
    expect(await screen.findByText("This page is for administrators.")).toBeInTheDocument();
    expect(calls.some((c) => c.path.startsWith("/admin"))).toBe(false);
  });

  it("adds an account directly, with no invite, and lists it", async () => {
    const calls = mockApi({
      ...asAdmin,
      "GET /admin/users": { body: USERS },
      "POST /admin/users": (call) => {
        const body = call.body as { email: string; role: string };
        if (body.email === "taken@example.com") {
          return { status: 409, body: { detail: "That email already has an account." } };
        }
        return { status: 201, body: account("u3", body.email, { role: body.role as "member" | "admin" }) };
      },
    });
    renderApp("/admin");
    const user = userEvent.setup();
    const form = await screen.findByRole("form", { name: "Add an account" });
    const add = within(form).getByRole("button", { name: "Add account" });
    await user.type(within(form).getByLabelText("Email"), "kid@example.com");
    await user.type(within(form).getByLabelText("Password"), "short");
    expect(add).toBeDisabled(); // under 12 characters
    await user.type(within(form).getByLabelText("Password"), "-but-longer-now");
    await user.selectOptions(within(form).getByLabelText("Role"), "admin");
    await user.click(add);
    expect(
      await screen.findByText("Created kid@example.com. They can sign in now with that password."),
    ).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST" && c.path === "/admin/users")?.body).toEqual({
      email: "kid@example.com",
      password: "short-but-longer-now",
      role: "admin",
    });
    expect(screen.getByRole("rowheader", { name: "kid@example.com" })).toBeInTheDocument();
    expect(within(form).getByLabelText("Email")).toHaveValue("");
    expect(within(form).getByLabelText("Password")).toHaveValue("");

    await user.type(within(form).getByLabelText("Email"), "taken@example.com");
    await user.type(within(form).getByLabelText("Password"), "long-enough-password");
    await user.click(add);
    expect(await screen.findByText("That email already has an account.")).toBeInTheDocument();
  });

  it("sets a member's password, but never an admin's", async () => {
    let fail = true;
    const calls = mockApi({
      ...asAdmin,
      "GET /admin/users": { body: [...USERS, account("a2", "other-admin@example.com", { role: "admin" })] },
      "POST /admin/users/u2/password": () =>
        fail
          ? { status: 422, body: { detail: "password must be at least 12 characters" } }
          : { body: account("u2", "bob@example.com", { email_verified: false }) },
    });
    renderApp("/admin");
    const user = userEvent.setup();
    const own = (await screen.findByText("root@example.com")).closest("tr") as HTMLElement;
    expect(within(own).queryByRole("button", { name: /Set password/ })).toBeNull();
    const otherAdmin = screen.getByText("other-admin@example.com").closest("tr") as HTMLElement;
    expect(within(otherAdmin).queryByRole("button", { name: /Set password/ })).toBeNull();

    const toggle = screen.getByRole("button", { name: "Set password for bob@example.com" });
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    let form = screen.getByRole("form", { name: "New password for bob@example.com" });
    const field = within(form).getByLabelText("New password for bob@example.com");
    expect(field).toHaveAttribute("autocomplete", "off");
    await user.type(field, "typed-then-cancelled");
    await user.click(within(form).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("form", { name: "New password for bob@example.com" })).toBeNull();
    expect(toggle).toHaveFocus();

    await user.click(toggle);
    form = screen.getByRole("form", { name: "New password for bob@example.com" });
    expect(within(form).getByLabelText("New password for bob@example.com")).toHaveValue("");
    await user.click(within(form).getByLabelText("New password for bob@example.com"));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("form", { name: "New password for bob@example.com" })).toBeNull();
    // The toggle closes it too.
    await user.click(toggle);
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");

    await user.click(toggle);
    form = screen.getByRole("form", { name: "New password for bob@example.com" });
    const save = within(form).getByRole("button", { name: "Save password" });
    expect(save).toBeDisabled();
    await user.type(within(form).getByLabelText("New password for bob@example.com"), "a-new-long-password");
    await user.click(save);
    // An error shows inside the open row, once.
    expect(await within(form).findByText("password must be at least 12 characters")).toBeInTheDocument();
    expect(screen.getAllByText("password must be at least 12 characters")).toHaveLength(1);

    fail = false;
    await user.click(save);
    expect(await screen.findByText("Password set; they have been signed out everywhere.")).toBeInTheDocument();
    expect(calls.filter((c) => c.path === "/admin/users/u2/password").at(-1)?.body).toEqual({
      password: "a-new-long-password",
    });
    expect(screen.queryByRole("form", { name: "New password for bob@example.com" })).toBeNull();
    expect(toggle).toHaveFocus();
  });

  it("manages users: role, suspend, unlock and delete, never the admin's own account", async () => {
    const calls = mockApi({
      ...asAdmin,
      "GET /admin/users": { body: USERS },
      "PATCH /admin/users/u2": { body: account("u2", "bob@example.com", { role: "admin" }) },
      "POST /admin/users/u2/suspend": { body: account("u2", "bob@example.com", { role: "admin", suspended: true }) },
      "POST /admin/users/u2/unsuspend": { body: account("u2", "bob@example.com", { role: "admin" }) },
      "POST /admin/users/u2/unlock": { body: account("u2", "bob@example.com", { role: "admin" }) },
      "DELETE /admin/users/u2": { status: 204 },
    });
    renderApp("/admin");
    const user = userEvent.setup();
    expect(await screen.findByRole("link", { name: "Admin" })).toBeInTheDocument();
    await waitFor(() => {
      expect(document.title).toBe("Admin · Flash-Cards");
    });
    const own = (await screen.findByText("root@example.com")).closest("tr") as HTMLElement;
    expect(within(own).getByText("(you)")).toBeInTheDocument();
    expect(within(own).getByRole("combobox")).toBeDisabled();
    expect(
      within(own)
        .getAllByRole("button")
        .map((b) => b.textContent),
    ).toEqual(["AI quota"]);
    expect(screen.getByText("Active, email not confirmed")).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("Role of bob@example.com"), "admin");
    await user.click(screen.getByRole("button", { name: "Make admin and share their decks" }));
    expect(await screen.findByText("Role set to admin.")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ role: "admin" });
    // Bob is an admin now, so suspending him takes his decks out of the library: it asks.
    await user.click(screen.getByRole("button", { name: "Suspend bob@example.com" }));
    const suspend = screen.getByRole("alertdialog", { name: "Suspend bob@example.com" });
    expect(suspend).toHaveAccessibleDescription(
      "Suspend bob@example.com? Their sessions end. Their decks leave the library for every member; members keep their progress.",
    );
    await user.click(within(suspend).getByRole("button", { name: "Suspend" }));
    expect(await screen.findByText(/Suspended; their sessions have ended/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Unsuspend bob@example.com" }));
    await user.click(screen.getByRole("button", { name: "Unsuspend and share their decks" }));
    expect(await screen.findByText("Unsuspended.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Unlock sign-in for bob@example.com" }));
    expect(await screen.findByText("Sign-in unlocked.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Delete bob@example.com" }));
    // Deleting an admin erases what members did on their decks too, and the dialog says so.
    expect(screen.getByRole("alertdialog", { name: "Delete bob@example.com" })).toHaveAccessibleDescription(
      "Delete bob@example.com and all their decks, cards and history, and every member's progress on their decks? This cannot be undone.",
    );
    await user.click(screen.getByRole("button", { name: "Delete account" }));
    await waitFor(() => {
      expect(screen.queryByText("bob@example.com")).not.toBeInTheDocument();
    });
  });

  it("asks before publishing an account's decks to every member, and says admins' decks are shared", async () => {
    const calls = mockApi({
      ...asAdmin,
      "GET /admin/users": {
        body: [
          ...USERS,
          account("u3", "eve@example.com", { role: "admin", suspended: true }),
          account("u4", "kim@example.com", { suspended: true }),
        ],
      },
      "PATCH /admin/users/u2": { body: account("u2", "bob@example.com", { role: "admin" }) },
      "POST /admin/users/u3/unsuspend": { body: account("u3", "eve@example.com", { role: "admin" }) },
      "POST /admin/users/u4/unsuspend": { body: account("u4", "kim@example.com") },
      "PATCH /admin/users/u3": { body: account("u3", "eve@example.com") },
    });
    renderApp("/admin");
    const user = userEvent.setup();
    expect(
      await screen.findByText(/Decks owned by an admin are shared with every member as a read-only library/),
    ).toBeInTheDocument();

    // Promoting asks first; Cancel and Escape change nothing and return focus.
    const role = screen.getByLabelText("Role of bob@example.com");
    await user.selectOptions(role, "admin");
    const ask = screen.getByRole("alertdialog", { name: "Make bob@example.com an admin" });
    expect(ask).toHaveTextContent(
      "Every deck they own, including decks made as a member, becomes visible to every member at once, read-only.",
    );
    expect(within(ask).getByRole("button", { name: "Cancel" })).toHaveFocus();
    expect(role).toHaveValue("member");
    await user.click(within(ask).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(role).toHaveFocus();
    await user.selectOptions(role, "admin");
    await user.keyboard("{Shift>}{Tab}{/Shift}"); // other keys leave the question open
    expect(screen.getByRole("button", { name: "Make admin and share their decks" })).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(role).toHaveFocus();
    expect(calls.some((c) => c.method === "PATCH")).toBe(false);

    // Unsuspending an admin asks too; focus comes back to the row once it is done.
    await user.click(screen.getByRole("button", { name: "Unsuspend eve@example.com" }));
    const again = screen.getByRole("alertdialog", { name: "Unsuspend eve@example.com" });
    expect(again).toHaveTextContent("They are an admin, so every deck they own becomes visible to every member again");
    await user.click(within(again).getByRole("button", { name: "Unsuspend and share their decks" }));
    expect(await screen.findByText("Unsuspended.")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Suspend eve@example.com" })).toHaveFocus();
    });

    // Making an admin a member takes their decks out of the library: that asks too.
    await user.selectOptions(screen.getByLabelText("Role of eve@example.com"), "member");
    const demote = screen.getByRole("alertdialog", { name: "Make eve@example.com a member" });
    expect(demote).toHaveAccessibleDescription(
      "Make eve@example.com a member? Their decks leave the library for every member; members keep their progress.",
    );
    await user.click(within(demote).getByRole("button", { name: "Make member" }));
    expect(await screen.findByText("Role set to member.")).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/admin/users/u3")?.body).toEqual({ role: "member" });

    // A member's decks are their own, so unsuspending or deleting one says nothing of the library.
    await user.click(screen.getByRole("button", { name: "Unsuspend kim@example.com" }));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Delete kim@example.com" }));
    expect(screen.getByRole("alertdialog", { name: "Delete kim@example.com" })).toHaveAccessibleDescription(
      "Delete kim@example.com and all their decks, cards and history? This cannot be undone.",
    );
    await waitFor(() => {
      expect(calls.some((c) => c.path === "/admin/users/u4/unsuspend")).toBe(true);
    });
  });

  it("shows refusals from the server and a list that cannot load", async () => {
    mockApi({
      ...asAdmin,
      "GET /admin/users": { body: USERS },
      "POST /admin/users/u2/suspend": { status: 409, body: { detail: "Cannot suspend the last admin." } },
      "DELETE /admin/users/u2": { status: 500 },
    });
    const { unmount } = renderApp("/admin");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Suspend bob@example.com" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Cannot suspend the last admin.");
    await user.click(screen.getByRole("button", { name: "Delete bob@example.com" }));
    await user.click(screen.getByRole("button", { name: "Delete account" }));
    await waitFor(() => {
      expect(screen.getAllByRole("alert").map((a) => a.textContent)).toEqual([
        "Cannot suspend the last admin.",
        "Something went wrong on the server. Try again.",
      ]);
    });
    unmount();
    mockApi({ ...asAdmin, "GET /admin/users": { status: 403, body: { detail: "Admins only." } } });
    renderApp("/admin");
    expect(await screen.findByRole("alert")).toHaveTextContent("Admins only.");
  });

  it("moves between tabs with the arrow keys, Home and End", async () => {
    mockApi({
      ...asAdmin,
      "GET /admin/users": { body: USERS },
      "GET /admin/invites": { body: [] },
      "GET /admin/audit": { body: [] },
    });
    renderApp("/admin");
    const user = userEvent.setup();
    const users = await screen.findByRole("tab", { name: "Users" });
    expect(users).toHaveAttribute("aria-selected", "true");
    users.focus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Invites" })).toHaveFocus();
    expect(screen.getByRole("tabpanel", { name: "Invites" })).toBeInTheDocument();
    await user.keyboard("{End}");
    expect(screen.getByRole("tab", { name: "Audit log" })).toHaveAttribute("aria-selected", "true");
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Users" })).toHaveFocus();
    await user.keyboard("{ArrowLeft}");
    expect(screen.getByRole("tab", { name: "Audit log" })).toHaveFocus();
    await user.keyboard("{Home}");
    expect(screen.getByRole("tab", { name: "Users" })).toHaveFocus();
    await user.keyboard("x");
    expect(screen.getByRole("tab", { name: "Users" })).toHaveFocus();
    await user.click(screen.getByRole("tab", { name: "Invites" }));
    expect(await screen.findByText("No invites yet.")).toBeInTheDocument();
  });

  it("creates an invite with a link shown once, and revokes an active one", async () => {
    let list = [invite("i1", { email: "new@example.com", role: "admin" }), invite("i0", { status: "redeemed" })];
    const calls = mockApi({
      ...asAdmin,
      "GET /admin/users": { body: USERS },
      "GET /admin/invites": () => ({ body: list }),
      "POST /admin/invites": {
        status: 201,
        body: { id: "i2", token: "tok", role: "member", email: null, expires_at: "2026-11-15T00:00:00Z" },
      },
      "POST /admin/invites/i1/revoke": () => {
        list = [invite("i1", { status: "revoked" }), invite("i0", { status: "redeemed" })];
        return { status: 204 };
      },
    });
    renderApp("/admin");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Invites" }));
    expect(await screen.findByText("new@example.com")).toBeInTheDocument();
    expect(screen.getByText("Anyone with the link")).toBeInTheDocument();
    const days = screen.getByLabelText("Valid for (days)");
    await user.clear(days);
    await user.type(days, "91");
    expect(screen.getByRole("button", { name: "Create invite" })).toBeDisabled();
    await user.clear(days);
    await user.type(days, "14");
    await user.selectOptions(screen.getByLabelText("Role"), "admin");
    await user.selectOptions(screen.getByLabelText("Role"), "member");
    await user.click(screen.getByRole("button", { name: "Create invite" }));
    expect(await screen.findByRole("textbox", { name: "Invite link" })).toHaveValue(inviteUrl("tok"));
    expect(inviteUrl("a b")).toBe(`${window.location.origin}/signup#token=a%20b`);
    expect(calls.find((c) => c.path === "/admin/invites" && c.method === "POST")?.body).toEqual({
      email: null,
      role: "member",
      ttl_days: 14,
    });
    await user.type(screen.getByLabelText(/Email/), " pat@example.com ");
    await user.click(screen.getByRole("button", { name: "Create invite" }));
    await waitFor(() => {
      expect(calls.filter((c) => c.path === "/admin/invites" && c.method === "POST")[1]?.body).toMatchObject({
        email: "pat@example.com",
      });
    });
    await user.click(screen.getByRole("button", { name: "Revoke" }));
    await user.click(screen.getByRole("button", { name: "Revoke invite" }));
    expect(await screen.findByText("revoked")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Revoke" })).not.toBeInTheDocument();
  });

  it("resends an emailed invite with a new link shown once, and reports a refusal", async () => {
    const calls = mockApi({
      ...asAdmin,
      "GET /admin/users": { body: USERS },
      "GET /admin/invites": {
        body: [
          invite("i1", { email: "new@example.com" }),
          invite("i2"),
          invite("i3", { email: "x@example.com", status: "expired" }),
        ],
      },
      "POST /admin/invites/i1/resend": {
        body: {
          id: "i1",
          token: "fresh",
          role: "member",
          email: "new@example.com",
          expires_at: "2026-11-08T00:00:00Z",
        },
      },
      "POST /admin/invites": {
        status: 201,
        body: { id: "i4", token: "tok", role: "member", email: null, expires_at: "2026-11-15T00:00:00Z" },
      },
      "POST /admin/invites/i1/revoke": { status: 204 },
      "POST /admin/invites/i2/revoke": { status: 204 },
    });
    renderApp("/admin");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Invites" }));
    // Only an active invite with an address can be emailed again.
    const resend = await screen.findByRole("button", { name: "Resend" });
    expect(screen.getAllByRole("button", { name: "Resend" })).toHaveLength(1);
    expect(resend.closest("tr")).toHaveTextContent("new@example.com");
    await user.click(resend);
    expect(screen.getByRole("alertdialog", { name: "Resend" })).toHaveTextContent(
      "Email a new sign-up link to new@example.com? The current link stops working",
    );
    await user.click(screen.getByRole("button", { name: "Resend invite" }));
    expect(await screen.findByRole("textbox", { name: "New invite link" })).toHaveValue(inviteUrl("fresh"));
    expect(screen.getByText(/Invite emailed again to new@example.com\./)).toBeInTheDocument();
    expect(calls.filter((c) => c.path === "/admin/invites/i1/resend")).toHaveLength(1);
    // Revoking another invite keeps the link; revoking this one removes it.
    const revokeIn = async (email: string): Promise<void> => {
      const row = screen.getByText(email).closest("tr") as HTMLElement;
      await user.click(within(row).getByRole("button", { name: "Revoke" }));
      await user.click(within(row).getByRole("button", { name: "Revoke invite" }));
    };
    await revokeIn("Anyone with the link");
    await waitFor(() => {
      expect(calls.filter((c) => c.path === "/admin/invites/i2/revoke")).toHaveLength(1);
    });
    expect(screen.getByRole("textbox", { name: "New invite link" })).toBeInTheDocument();
    await revokeIn("new@example.com");
    await waitFor(() => {
      expect(screen.queryByRole("textbox", { name: "New invite link" })).not.toBeInTheDocument();
    });
    // A new invite replaces the resent link on screen.
    await user.click(screen.getByRole("button", { name: "Resend" }));
    await user.click(screen.getByRole("button", { name: "Resend invite" }));
    expect(await screen.findByRole("textbox", { name: "New invite link" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Create invite" }));
    expect(await screen.findByRole("textbox", { name: "Invite link" })).toHaveValue(inviteUrl("tok"));
    expect(screen.queryByRole("textbox", { name: "New invite link" })).not.toBeInTheDocument();
  });

  it("shows the server's answer when an invite cannot be resent", async () => {
    mockApi({
      ...asAdmin,
      "GET /admin/users": { body: USERS },
      "GET /admin/invites": { body: [invite("i1", { email: "new@example.com" })] },
      "POST /admin/invites/i1/resend": {
        status: 409,
        body: { detail: "This invite has no email address; share its link instead." },
      },
    });
    renderApp("/admin");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Invites" }));
    await user.click(await screen.findByRole("button", { name: "Resend" }));
    await user.click(screen.getByRole("button", { name: "Resend invite" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This invite has no email address; share its link instead.",
    );
    expect(screen.queryByRole("textbox", { name: "New invite link" })).not.toBeInTheDocument();
  });

  it("reports invites that cannot load and a revoke that fails", async () => {
    mockApi({
      ...asAdmin,
      "GET /admin/users": { body: USERS },
      "GET /admin/invites": { body: [invite("i1")] },
      "POST /admin/invites/i1/revoke": { status: 404, body: { detail: "No active invite with that id." } },
    });
    const { unmount } = renderApp("/admin");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Invites" }));
    await user.click(await screen.findByRole("button", { name: "Revoke" }));
    await user.click(screen.getByRole("button", { name: "Revoke invite" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("No active invite with that id.");
    unmount();
    mockApi({ ...asAdmin, "GET /admin/users": { body: USERS }, "GET /admin/invites": { status: 500 } });
    renderApp("/admin");
    await user.click(await screen.findByRole("tab", { name: "Invites" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong");
  });

  it("pages through the audit log, naming people by email and showing detail as text", async () => {
    const full = Array.from({ length: AUDIT_PAGE }, (_, n) => event(n));
    const calls = mockApi({
      ...asAdmin,
      "GET /admin/users": { body: USERS },
      "GET /admin/audit?limit=50": { body: full },
      [`GET /admin/audit?limit=50&before=e${String(AUDIT_PAGE - 1)}`]: { body: [] },
    });
    const { container } = renderApp("/admin");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Audit log" }));
    expect(await screen.findByText("user.suspend")).toBeInTheDocument();
    const first = screen.getByText("user.suspend").closest("tr") as HTMLElement;
    await waitFor(() => {
      expect(within(first).getByText("root@example.com")).toBeInTheDocument();
    });
    expect(within(first).getByText("bob@example.com")).toBeInTheDocument();
    expect(within(first).getByText("reason: <b>spam</b>")).toBeInTheDocument();
    expect(container.querySelector("td b")).toBeNull();
    expect(screen.getByText("gone")).toBeInTheDocument();
    expect(screen.getAllByText("System").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "Newer" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Older" }));
    expect(await screen.findByText("No events before these.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Older" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Newer" }));
    expect(await screen.findByText("user.suspend")).toBeInTheDocument();
    expect(calls.filter((c) => c.path.startsWith("/admin/audit")).map((c) => c.path)).toEqual([
      "/admin/audit?limit=50",
      "/admin/audit?limit=50&before=e49",
      "/admin/audit?limit=50",
    ]);
  });

  it("says when the audit log is empty or cannot load", async () => {
    mockApi({ ...asAdmin, "GET /admin/users": { status: 500 }, "GET /admin/audit": { body: [] } });
    const { unmount } = renderApp("/admin");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Audit log" }));
    expect(await screen.findByText("No events yet.")).toBeInTheDocument();
    unmount();
    mockApi({ ...asAdmin, "GET /admin/users": { body: USERS }, "GET /admin/audit": { status: 500 } });
    renderApp("/admin");
    await user.click(await screen.findByRole("tab", { name: "Audit log" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong");
  });
});
