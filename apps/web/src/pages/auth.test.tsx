// SPDX-License-Identifier: AGPL-3.0-only
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { useAuth, AuthProvider } from "../auth/AuthContext";
import { USER, mockApi, renderApp } from "../test/helpers";

const signedOut = { "GET /auth/me": { status: 401, body: { detail: "Not authenticated." } } };

describe("sign-in", () => {
  it("sends a signed-out visitor to sign-in and back to where they were going", async () => {
    let signedIn = false;
    const calls = mockApi({
      "GET /auth/me": () => (signedIn ? { body: USER } : { status: 401, body: { detail: "x" } }),
      "POST /auth/login": () => {
        signedIn = true;
        return { body: USER };
      },
      "GET /decks": { body: [] },
      "GET /cards": { body: { total: 0, cards: [] } },
    });
    renderApp("/cards?tag=bio");
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Email"), "ada@example.com");
    await user.type(screen.getByLabelText("Password"), "correct-horse-battery");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("heading", { name: "Browse cards" })).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/auth/login")?.body).toEqual({
      email: "ada@example.com",
      password: "correct-horse-battery",
    });
    expect(localStorage.getItem("flashcards.lastUser")).toContain("ada@example.com");
  });

  it("shows the server's reason when sign-in fails", async () => {
    mockApi({
      ...signedOut,
      "POST /auth/login": { status: 429, body: { detail: "Too many failed sign-in attempts." } },
    });
    renderApp("/login");
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Email"), "a@b.co");
    await user.type(screen.getByLabelText("Password"), "x");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many failed sign-in attempts.");
  });

  it("skips the form for someone already signed in", async () => {
    mockApi({ "GET /auth/me": { body: USER }, "GET /decks": { body: [] } });
    renderApp("/login?next=//evil.example");
    expect(await screen.findByRole("heading", { name: "Decks" })).toBeInTheDocument();
  });
});

describe("invite sign-up", () => {
  it("creates the account with the token from the link fragment", async () => {
    const calls = mockApi({
      ...signedOut,
      "POST /auth/signup": { status: 201, body: USER },
      "GET /decks": { body: [] },
    });
    renderApp("/signup#token=inv-123");
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Email"), "ada@example.com");
    await user.type(screen.getByLabelText("Password"), "correct-horse-battery");
    expect(screen.getByLabelText("Password")).toHaveAccessibleDescription("At least 12 characters.");
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByRole("heading", { name: "Decks" })).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/auth/signup")?.body).toEqual({
      token: "inv-123",
      email: "ada@example.com",
      password: "correct-horse-battery",
    });
  });

  it("explains that an invitation link is needed", async () => {
    mockApi(signedOut);
    renderApp("/signup");
    expect(await screen.findByText(/needs the invitation link/)).toBeInTheDocument();
  });

  it("shows a refused invite", async () => {
    mockApi({ ...signedOut, "POST /auth/signup": { status: 400, body: { detail: "This invite is not valid." } } });
    renderApp("/signup?token=old");
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Email"), "ada@example.com");
    await user.type(screen.getByLabelText("Password"), "correct-horse-battery");
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This invite is not valid.");
  });
});

describe("email verification", () => {
  it("confirms the address once and refreshes the user", async () => {
    let verified = false;
    const calls = mockApi({
      "GET /auth/me": () => ({ body: { ...USER, email_verified: verified } }),
      "POST /auth/verify-email": () => {
        verified = true;
        return { status: 204 };
      },
    });
    renderApp("/verify-email#token=v1");
    expect(await screen.findByText("Your email address is confirmed.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to your decks" })).toBeInTheDocument();
    expect(calls.filter((c) => c.path === "/auth/verify-email")).toHaveLength(1);
    await waitFor(() => {
      expect(calls.filter((c) => c.path === "/auth/me").length).toBeGreaterThanOrEqual(2);
    });
  });

  it("reports an invalid link", async () => {
    mockApi({
      ...signedOut,
      "POST /auth/verify-email": { status: 422, body: { detail: "This link is invalid or has expired." } },
    });
    renderApp("/verify-email#token=bad");
    expect(await screen.findByRole("alert")).toHaveTextContent("This link is invalid or has expired.");
    expect(screen.getByRole("link", { name: "Sign in" })).toBeInTheDocument();
  });

  it("reports a missing token without calling the server", async () => {
    const calls = mockApi(signedOut);
    renderApp("/verify-email");
    expect(await screen.findByRole("alert")).toHaveTextContent("invalid or has expired");
    expect(calls.some((c) => c.path === "/auth/verify-email")).toBe(false);
  });
});

describe("password reset", () => {
  it("requests a reset link without saying whether the account exists", async () => {
    const calls = mockApi({ ...signedOut, "POST /auth/password-reset": { status: 202 } });
    renderApp("/forgot-password");
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Email"), "ada@example.com");
    await user.click(screen.getByRole("button", { name: "Send reset link" }));
    expect(await screen.findByText(/If that address has an account/)).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/auth/password-reset")?.body).toEqual({ email: "ada@example.com" });
  });

  it("shows a failure to request a link", async () => {
    mockApi({ ...signedOut, "POST /auth/password-reset": new TypeError("offline") });
    renderApp("/forgot-password");
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Email"), "ada@example.com");
    await user.click(screen.getByRole("button", { name: "Send reset link" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not reach the server");
  });

  it("sets a new password with the token from the link", async () => {
    const calls = mockApi({ ...signedOut, "POST /auth/password-reset/confirm": { status: 204 } });
    renderApp("/reset-password#token=r1");
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("New password"), "another-long-password");
    await user.click(screen.getByRole("button", { name: "Change password" }));
    expect(await screen.findByText(/Your password has been changed/)).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/auth/password-reset/confirm")?.body).toEqual({
      token: "r1",
      password: "another-long-password",
    });
  });

  it("shows a weak password or a dead link", async () => {
    mockApi({
      ...signedOut,
      "POST /auth/password-reset/confirm": { status: 422, body: { detail: "password must be at least 12 characters" } },
    });
    renderApp("/reset-password#token=r1");
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("New password"), "short");
    await user.click(screen.getByRole("button", { name: "Change password" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("at least 12 characters");
  });

  it("rejects a reset page opened without a token", async () => {
    mockApi(signedOut);
    renderApp("/reset-password");
    expect(await screen.findByRole("alert")).toHaveTextContent("invalid or has expired");
  });
});

describe("AuthProvider", () => {
  function Probe() {
    const { loading, user } = useAuth();
    return <p>{loading ? "loading" : (user?.email ?? "nobody")}</p>;
  }

  it("opens offline as the last signed-in user", async () => {
    localStorage.setItem("flashcards.lastUser", JSON.stringify(USER));
    mockApi({ "GET /auth/me": new TypeError("offline") });
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );
    expect(await screen.findByText("ada@example.com")).toBeInTheDocument();
  });

  it("opens offline as nobody when nothing usable is stored", async () => {
    localStorage.setItem("flashcards.lastUser", "{oops");
    mockApi({ "GET /auth/me": new TypeError("offline") });
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );
    expect(await screen.findByText("nobody")).toBeInTheDocument();
  });

  it("opens offline as nobody on a fresh device", async () => {
    mockApi({ "GET /auth/me": new TypeError("offline") });
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );
    expect(await screen.findByText("nobody")).toBeInTheDocument();
  });

  it("ignores an answer that arrives after it is gone", async () => {
    let answer: (value: { body: typeof USER }) => void = () => undefined;
    mockApi({ "GET /auth/me": () => new Promise((resolve) => (answer = resolve)) });
    const { unmount } = render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );
    unmount();
    answer({ body: USER });
    await Promise.resolve();
    expect(localStorage.getItem("flashcards.lastUser")).toBeNull();
  });

  it("keeps working when storage is blocked", async () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    mockApi({ "GET /auth/me": { body: USER } });
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );
    expect(await screen.findByText("ada@example.com")).toBeInTheDocument();
  });

  it("must wrap anything that reads the session", () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    expect(() => render(<Probe />)).toThrow("useAuth must be used inside AuthProvider");
  });
});

describe("public sign-up", () => {
  const open = { ...signedOut, "GET /auth/options": { body: { invite_only: false } } };

  it("links to sign-up from sign-in when the server takes public sign-ups", async () => {
    mockApi(open);
    renderApp("/login");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("link", { name: "Create an account" }));
    expect(await screen.findByRole("heading", { name: "Create your account" })).toBeInTheDocument();
    expect(screen.queryByText(/invite-only/)).toBeNull();
  });

  it("says sign-up is invite-only when the server says so", async () => {
    mockApi({ ...signedOut, "GET /auth/options": { body: { invite_only: true } } });
    renderApp("/login");
    expect(await screen.findByText(/Flash-Cards is invite-only/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Create an account" })).toBeNull();
  });

  it("registers, never signs in, and asks to check email", async () => {
    const calls = mockApi({
      ...open,
      "POST /auth/register": { status: 202, body: { detail: "If the address can be registered, an email is on its way." } },
    });
    renderApp("/signup");
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Email"), "new@example.com");
    await user.type(screen.getByLabelText("Password"), "correct-horse-battery");
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByRole("heading", { name: "Check your email" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("a confirmation link is on its way");
    expect(calls.find((c) => c.path === "/auth/register")?.body).toEqual({
      email: "new@example.com",
      password: "correct-horse-battery",
    });
    expect(calls.some((c) => c.path === "/auth/signup" || c.path === "/auth/login")).toBe(false);
    expect(localStorage.getItem("flashcards.lastUser")).toBeNull();
    expect(screen.queryByLabelText("Password")).toBeNull();
  });

  it("shows a refused password", async () => {
    mockApi({ ...open, "POST /auth/register": { status: 422, body: { detail: "Password is too short." } } });
    renderApp("/signup");
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Email"), "new@example.com");
    await user.type(screen.getByLabelText("Password"), "short");
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Password is too short.");
  });

  it("explains when sign-up closed in the meantime", async () => {
    mockApi({ ...open, "POST /auth/register": { status: 404, body: { detail: "Not found." } } });
    renderApp("/signup");
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Email"), "new@example.com");
    await user.type(screen.getByLabelText("Password"), "correct-horse-battery");
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Flash-Cards is invite-only.");
  });

  it("still uses the invitation when the link has one", async () => {
    const calls = mockApi({ ...open, "POST /auth/signup": { status: 201, body: USER }, "GET /decks": { body: [] } });
    renderApp("/signup#token=inv-9");
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Email"), "ada@example.com");
    await user.type(screen.getByLabelText("Password"), "correct-horse-battery");
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByRole("heading", { name: "Decks" })).toBeInTheDocument();
    expect(calls.some((c) => c.path === "/auth/register")).toBe(false);
  });
});
