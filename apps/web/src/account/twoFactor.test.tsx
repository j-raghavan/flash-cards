// SPDX-License-Identifier: AGPL-3.0-only
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { TwoFactorStatus } from "../api/twoFactor";
import { recoveryCodesText } from "../api/twoFactor";
import { USER, mockApi, renderApp, type Call } from "../test/helpers";
import { proofFrom } from "./TwoFactorPanel";

const webauthn = vi.hoisted(() => ({
  startAuthentication: vi.fn(),
  startRegistration: vi.fn(),
}));
vi.mock("@simplewebauthn/browser", () => webauthn);

const OFF: TwoFactorStatus = { enabled: false, totp: false, totp_available: true, passkeys: [], recovery_codes_left: 0 };
const LAPTOP = { id: "p1", name: "Laptop", created_at: "2026-11-01T09:00:00Z", last_used_at: null };
const PHONE = { id: "p2", name: "Phone", created_at: "2026-11-02T09:00:00Z", last_used_at: "2026-11-05T09:00:00Z" };
const CODES = Array.from({ length: 10 }, (_, i) => `aaaa-bbbb-cccc-${String(1000 + i)}`);

type Routes = Parameters<typeof mockApi>[0];

function api(status: () => TwoFactorStatus, routes: Routes = {}): Call[] {
  return mockApi({
    "GET /auth/me": { body: USER },
    "GET /auth/sessions": { body: [] },
    "GET /account/ai-quota": { body: { period: "2026-11", monthly_pages: 0, used_pages: 0 } },
    "GET /legal/ai-disclosure": { body: { ai_enabled: false, trains_on_user_content: false, statements: [], provider: null } },
    "GET /auth/two-factor": () => ({ body: status() }),
    ...routes,
  });
}

async function openPanel(): Promise<HTMLElement> {
  renderApp("/account");
  const heading = await screen.findByRole("heading", { name: "Two-step sign-in" });
  const section = heading.closest("section");
  if (section === null) {
    throw new Error("no section");
  }
  await within(section).findByRole("heading", { name: "Passkeys" });
  return section;
}

beforeEach(() => {
  webauthn.startRegistration.mockReset();
});

describe("two-step sign-in on the account page", () => {
  it("sets up an authenticator app and shows recovery codes once", async () => {
    let status = OFF;
    const createObjectURL = vi.fn(() => "blob:codes");
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() }));
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    const calls = api(() => status, {
      "POST /auth/two-factor/totp": (call) =>
        (call.body as { password?: string }).password === "correct-horse-battery"
          ? { body: { secret: "JBSWY3DPEHPK3PXP", uri: "otpauth://totp/Flash-Cards:ada?secret=JBSWY3DPEHPK3PXP" } }
          : { status: 403, body: { detail: "That password or code is not right." } },
      "POST /auth/two-factor/totp/confirm": (call) => {
        if ((call.body as { code: string }).code !== "123456") {
          return { status: 422, body: { detail: "That code is not right." } };
        }
        status = { ...OFF, enabled: true, totp: true, recovery_codes_left: 10 };
        return { body: { recovery_codes: CODES } };
      },
    });
    const section = await openPanel();
    const user = userEvent.setup();
    expect(within(section).getByText(/Two-step sign-in is off/)).toBeInTheDocument();
    await user.click(within(section).getByRole("button", { name: "Set up authenticator app" }));
    await user.type(within(section).getByLabelText("Your password"), "wrong-password-here");
    await user.click(within(section).getByRole("button", { name: "Continue" }));
    expect(await within(section).findByRole("alert")).toHaveTextContent("That password or code is not right.");
    await user.clear(within(section).getByLabelText("Your password"));
    await user.type(within(section).getByLabelText("Your password"), "correct-horse-battery");
    await user.click(within(section).getByRole("button", { name: "Continue" }));
    expect(await within(section).findByRole("img", { name: "QR code for your authenticator app" })).toBeInTheDocument();
    expect(within(section).getByDisplayValue("JBSWY3DPEHPK3PXP")).toBeInTheDocument();
    await user.type(within(section).getByLabelText("The 6-digit code your app shows"), "000000");
    await user.click(within(section).getByRole("button", { name: "Turn on" }));
    expect(await within(section).findByRole("alert")).toHaveTextContent("That code is not right.");
    await user.clear(within(section).getByLabelText("The 6-digit code your app shows"));
    await user.type(within(section).getByLabelText("The 6-digit code your app shows"), "123456");
    await user.click(within(section).getByRole("button", { name: "Turn on" }));
    const codes = await within(section).findByRole("region", { name: "Your recovery codes" });
    expect(within(codes).getAllByRole("listitem")).toHaveLength(10);
    await user.click(within(codes).getByRole("button", { name: "Download codes" }));
    expect(createObjectURL).toHaveBeenCalled();
    expect(click).toHaveBeenCalled();
    await user.click(within(codes).getByRole("button", { name: "I have saved them" }));
    expect(await within(section).findByText("On.")).toBeInTheDocument();
    expect(within(section).getByText("Your authenticator app is on.")).toBeInTheDocument();
    expect(within(section).getByText("10 unused recovery codes left.")).toBeInTheDocument();
    expect(calls.filter((c) => c.path === "/auth/two-factor/totp").map((c) => c.body)).toEqual([
      { password: "wrong-password-here" },
      { password: "correct-horse-battery" },
    ]);
    click.mockRestore();
    vi.unstubAllGlobals();
  });

  it("cancels a setup", async () => {
    api(() => OFF, {
      "POST /auth/two-factor/totp": { body: { secret: "S", uri: "otpauth://totp/x?secret=S" } },
    });
    const section = await openPanel();
    const user = userEvent.setup();
    await user.click(within(section).getByRole("button", { name: "Set up authenticator app" }));
    await user.click(within(section).getByRole("button", { name: "Cancel" }));
    await user.click(within(section).getByRole("button", { name: "Set up authenticator app" }));
    await user.type(within(section).getByLabelText("Your password"), "correct-horse-battery");
    await user.click(within(section).getByRole("button", { name: "Continue" }));
    await user.click(await within(section).findByRole("button", { name: "Cancel" }));
    expect(within(section).getByRole("button", { name: "Set up authenticator app" })).toBeInTheDocument();
  });

  it("turns the app off with a current code", async () => {
    let status: TwoFactorStatus = { ...OFF, enabled: true, totp: true, recovery_codes_left: 1 };
    const calls = api(() => status, {
      "DELETE /auth/two-factor/totp": () => {
        status = OFF;
        return { status: 204 };
      },
    });
    const section = await openPanel();
    const user = userEvent.setup();
    expect(within(section).getByText("1 unused recovery code left.")).toBeInTheDocument();
    await user.click(within(section).getByRole("button", { name: "Turn off authenticator app" }));
    await user.type(within(section).getByLabelText("Your password, or a code from your authenticator app"), "654321");
    await user.click(within(section).getByRole("button", { name: "Turn off" }));
    expect(await within(section).findByText("Your authenticator app is off.")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "DELETE")?.body).toEqual({ code: "654321" });
  });

  it("adds a passkey and shows codes when it turns two-step sign-in on", async () => {
    let status = OFF;
    webauthn.startRegistration.mockResolvedValue({ id: "new" });
    const calls = api(() => status, {
      "POST /auth/two-factor/passkeys/options": { body: { token: "reg", options: { challenge: "c" } } },
      "POST /auth/two-factor/passkeys": () => {
        status = { ...OFF, enabled: true, passkeys: [LAPTOP], recovery_codes_left: 10 };
        return { status: 201, body: { passkey: LAPTOP, recovery_codes: CODES } };
      },
    });
    const section = await openPanel();
    const user = userEvent.setup();
    expect(within(section).getByText("No passkeys.")).toBeInTheDocument();
    await user.click(within(section).getByRole("button", { name: "Add a passkey" }));
    await user.type(within(section).getByLabelText("Name (for example, the device it is on)"), " Laptop ");
    await user.type(within(section).getByLabelText("Your password"), "correct-horse-battery");
    await user.click(within(section).getByRole("button", { name: "Add passkey" }));
    const codes = await within(section).findByRole("region", { name: "Your recovery codes" });
    await user.click(within(codes).getByRole("button", { name: "I have saved them" }));
    expect(await within(section).findByText("Laptop")).toBeInTheDocument();
    expect(within(section).getByText(/Not used yet/)).toBeInTheDocument();
    expect(webauthn.startRegistration).toHaveBeenCalledWith({ optionsJSON: { challenge: "c" } });
    expect(calls.find((c) => c.path === "/auth/two-factor/passkeys")?.body).toEqual({
      token: "reg",
      credential: { id: "new" },
      name: "Laptop",
    });
  });

  it("adds another passkey without new codes, and reports a cancelled one", async () => {
    let status: TwoFactorStatus = { ...OFF, enabled: true, totp: true, passkeys: [LAPTOP], recovery_codes_left: 8 };
    const cancelled = new Error("no");
    cancelled.name = "NotAllowedError";
    webauthn.startRegistration.mockRejectedValueOnce(cancelled).mockResolvedValueOnce({ id: "second" });
    api(() => status, {
      "POST /auth/two-factor/passkeys/options": { body: { token: "reg", options: { challenge: "c" } } },
      "POST /auth/two-factor/passkeys": () => {
        status = { ...status, passkeys: [LAPTOP, PHONE] };
        return { status: 201, body: { passkey: PHONE, recovery_codes: null } };
      },
    });
    const section = await openPanel();
    const user = userEvent.setup();
    await user.click(within(section).getByRole("button", { name: "Add a passkey" }));
    const proof = within(section).getByLabelText("Your password, or a code from your authenticator app");
    await user.type(proof, "correct-horse-battery");
    await user.click(within(section).getByRole("button", { name: "Add passkey" }));
    expect(await within(section).findByRole("alert")).toHaveTextContent("cancelled or timed out");
    await user.click(within(section).getByRole("button", { name: "Add passkey" }));
    expect(await within(section).findByText("Added Phone.")).toBeInTheDocument();
    expect(within(section).getByText(/Last used/)).toBeInTheDocument();
  });

  it("removes a passkey", async () => {
    let status: TwoFactorStatus = { ...OFF, enabled: true, passkeys: [LAPTOP], recovery_codes_left: 10 };
    const calls = api(() => status, {
      "DELETE /auth/two-factor/passkeys/p1": () => {
        status = OFF;
        return { status: 204 };
      },
    });
    const section = await openPanel();
    const user = userEvent.setup();
    await user.click(within(section).getByRole("button", { name: "Remove passkey Laptop" }));
    await user.click(within(section).getByRole("button", { name: "Cancel" }));
    await user.click(within(section).getByRole("button", { name: "Remove passkey Laptop" }));
    await user.type(within(section).getByLabelText("Your password"), "correct-horse-battery");
    await user.click(within(section).getByRole("button", { name: "Remove Laptop" }));
    expect(await within(section).findByText("Removed Laptop.")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "DELETE")?.body).toEqual({ password: "correct-horse-battery" });
  });

  it("issues new recovery codes", async () => {
    const status: TwoFactorStatus = { ...OFF, enabled: true, passkeys: [LAPTOP], recovery_codes_left: 2 };
    api(() => status, {
      "POST /auth/two-factor/recovery-codes": { body: { recovery_codes: CODES } },
    });
    const section = await openPanel();
    const user = userEvent.setup();
    await user.click(within(section).getByRole("button", { name: "Get new recovery codes" }));
    await user.click(within(section).getByRole("button", { name: "Cancel" }));
    await user.click(within(section).getByRole("button", { name: "Get new recovery codes" }));
    await user.type(within(section).getByLabelText("Your password"), "correct-horse-battery");
    await user.click(within(section).getByRole("button", { name: "Get new codes" }));
    expect(await within(section).findByRole("region", { name: "Your recovery codes" })).toBeInTheDocument();
  });

  it("says when the server offers no authenticator apps", async () => {
    api(() => ({ ...OFF, totp_available: false }));
    const section = await openPanel();
    expect(within(section).getByText("Authenticator apps are not available on this server.")).toBeInTheDocument();
    expect(within(section).queryByRole("button", { name: "Set up authenticator app" })).not.toBeInTheDocument();
  });

  it("tells codes from passwords and writes a codes file", () => {
    expect(proofFrom(" 123456 ")).toEqual({ code: "123456" });
    expect(proofFrom("correct-horse-battery")).toEqual({ password: "correct-horse-battery" });
    expect(recoveryCodesText(["a", "b"], "ada@example.com")).toContain("ada@example.com\n");
  });
});
