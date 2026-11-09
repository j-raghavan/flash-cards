// SPDX-License-Identifier: AGPL-3.0-only
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { onUnauthorized } from "../api/client";
import { USER, mockApi, renderApp } from "../test/helpers";
import { passkeyError } from "./SecondFactorStep";

const webauthn = vi.hoisted(() => ({
  startAuthentication: vi.fn(),
  startRegistration: vi.fn(),
}));
vi.mock("@simplewebauthn/browser", () => webauthn);

const ASSERTION = { id: "cred", rawId: "cred", type: "public-key", response: {}, clientExtensionResults: {} };

function pendingLogin(methods: string[]) {
  return { status: 202, body: { second_factor_required: true, token: "tok", methods } };
}

async function signInWithPassword(): Promise<ReturnType<typeof userEvent.setup>> {
  renderApp("/login");
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText("Email"), "ada@example.com");
  await user.type(screen.getByLabelText("Password"), "correct-horse-battery");
  await user.click(screen.getByRole("button", { name: "Sign in" }));
  expect(await screen.findByRole("heading", { name: "Confirm it's you" })).toBeInTheDocument();
  return user;
}

beforeEach(() => {
  webauthn.startAuthentication.mockReset();
});

describe("second sign-in step", () => {
  it("finishes with an authenticator code, and a wrong code keeps the user signed out", async () => {
    let signedIn = false;
    const unauthorized = vi.fn();
    const calls = mockApi({
      "GET /auth/me": () => (signedIn ? { body: USER } : { status: 401, body: { detail: "x" } }),
      "POST /auth/login": pendingLogin(["totp", "recovery_code"]),
      "POST /auth/login/second-factor": (call) => {
        if ((call.body as { code: string }).code !== "123456") {
          return { status: 401, body: { detail: "That code is not right." } };
        }
        signedIn = true;
        return { body: USER };
      },
      "GET /decks": { body: [] },
    });
    const user = await signInWithPassword();
    onUnauthorized(unauthorized);
    const field = screen.getByLabelText("Code from your authenticator app, or a recovery code");
    await user.type(field, "000000");
    await user.click(screen.getByRole("button", { name: "Verify" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("That code is not right.");
    expect(unauthorized).not.toHaveBeenCalled();
    await user.clear(field);
    await user.type(field, " 123456 ");
    await user.click(screen.getByRole("button", { name: "Verify" }));
    expect(await screen.findByRole("heading", { name: "Decks" })).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/auth/login/second-factor" && c.body !== undefined)?.body).toEqual({
      token: "tok",
      code: "000000",
    });
    expect(screen.queryByRole("button", { name: "Use a passkey" })).not.toBeInTheDocument();
  });

  it("finishes with a passkey", async () => {
    let signedIn = false;
    webauthn.startAuthentication.mockResolvedValue(ASSERTION);
    const calls = mockApi({
      "GET /auth/me": () => (signedIn ? { body: USER } : { status: 401, body: { detail: "x" } }),
      "POST /auth/login": pendingLogin(["passkey"]),
      "POST /auth/login/passkey/options": { body: { options: { challenge: "abc" } } },
      "POST /auth/login/passkey": () => {
        signedIn = true;
        return { body: USER };
      },
      "GET /decks": { body: [] },
    });
    const user = await signInWithPassword();
    expect(screen.queryByRole("button", { name: "Verify" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Use a passkey" }));
    expect(await screen.findByRole("heading", { name: "Decks" })).toBeInTheDocument();
    expect(webauthn.startAuthentication).toHaveBeenCalledWith({ optionsJSON: { challenge: "abc" } });
    expect(calls.find((c) => c.path === "/auth/login/passkey")?.body).toEqual({ token: "tok", credential: ASSERTION });
  });

  it("explains a cancelled passkey and offers both ways", async () => {
    const cancelled = new Error("cancelled");
    cancelled.name = "NotAllowedError";
    webauthn.startAuthentication.mockRejectedValue(cancelled);
    mockApi({
      "GET /auth/me": { status: 401, body: { detail: "x" } },
      "POST /auth/login": pendingLogin(["totp", "passkey"]),
      "POST /auth/login/passkey/options": { body: { options: { challenge: "abc" } } },
    });
    const user = await signInWithPassword();
    expect(screen.getByLabelText("Code from your authenticator app")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Use a passkey" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("cancelled or timed out");
  });

  it("starts over when the pending sign-in has expired", async () => {
    mockApi({
      "GET /auth/me": { status: 401, body: { detail: "x" } },
      "POST /auth/login": pendingLogin(["recovery_code"]),
      "POST /auth/login/second-factor": {
        status: 401,
        body: { detail: "This sign-in has expired. Sign in again." },
      },
    });
    const user = await signInWithPassword();
    await user.type(screen.getByLabelText("Recovery code"), "abcd-efgh-ijkm-npqr");
    await user.click(screen.getByRole("button", { name: "Verify" }));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByText("This sign-in has expired. Sign in again.")).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toHaveValue("");
  });

  it("shows other failures in place and can start over", async () => {
    mockApi({
      "GET /auth/me": { status: 401, body: { detail: "x" } },
      "POST /auth/login": pendingLogin(["totp"]),
      "POST /auth/login/second-factor": { status: 429, body: { detail: "Too many failed sign-in attempts." } },
    });
    const user = await signInWithPassword();
    await user.type(screen.getByLabelText("Code from your authenticator app"), "111111");
    await user.click(screen.getByRole("button", { name: "Verify" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many failed sign-in attempts.");
    await user.click(screen.getByRole("button", { name: "Start over" }));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });

  it("turns WebAuthn errors into messages", () => {
    const aborted = new Error("x");
    aborted.name = "AbortError";
    expect(passkeyError(aborted).message).toContain("cancelled");
    const other = new Error("security");
    expect(passkeyError(other)).toBe(other);
    expect(passkeyError("odd").message).toBe("The passkey could not be used.");
  });
});
