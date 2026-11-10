// SPDX-License-Identifier: AGPL-3.0-only
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AiDisclosure, AiQuota, DeviceSession } from "../api/account";
import { aiNoticeSeen, forgetAiNotice, rememberAiNoticeSeen, safeTermsUrl } from "../api/account";
import { enqueue, pending, resetForTests } from "../offline/reviewQueue";
import { USER, mockApi, renderApp, type Call } from "../test/helpers";
import { formatPeriod } from "./AccountPage";

const THIS_DEVICE: DeviceSession = {
  id: "s1",
  created_at: "2026-11-01T09:00:00Z",
  expires_at: "2026-12-01T09:00:00Z",
  last_seen_at: "2026-11-08T09:00:00Z",
  device: "Firefox on macOS",
  current: true,
};
const PHONE: DeviceSession = {
  id: "s2",
  created_at: "2026-10-20T09:00:00Z",
  expires_at: "2026-11-20T09:00:00Z",
  last_seen_at: null,
  device: "Safari on iPhone",
  current: false,
};
const QUOTA: AiQuota = {
  period: "2026-11",
  monthly_pages: 200,
  used_pages: 37,
  max_document_pages: 300,
  quota_applies: true,
};
const DISCLOSURE: AiDisclosure = {
  ai_enabled: true,
  trains_on_user_content: false,
  statements: ["Flash-Cards never uses your content to train AI models.", "Only what a feature needs is sent."],
  provider: { name: "Anthropic", terms_url: "https://www.anthropic.com/legal/commercial-terms" },
};

type Routes = Parameters<typeof mockApi>[0];

function accountApi(overrides: Routes = {}): Call[] {
  return mockApi({
    "GET /auth/me": { body: USER },
    "GET /auth/sessions": { body: [THIS_DEVICE, PHONE] },
    "GET /account/ai-quota": { body: QUOTA },
    "GET /legal/ai-disclosure": { body: DISCLOSURE },
    "GET /auth/two-factor": {
      body: { enabled: false, totp: false, totp_available: true, passkeys: [], recovery_codes_left: 0 },
    },
    ...overrides,
  });
}

async function openAccount(): Promise<void> {
  renderApp("/account");
  expect(await screen.findByRole("heading", { name: "Account", level: 1 })).toBeInTheDocument();
}

function panel(name: string): HTMLElement {
  return screen.getByRole("region", { name });
}

afterEach(() => {
  vi.useRealTimers();
});

describe("account page", () => {
  it("is linked from the main navigation and shows every section", async () => {
    accountApi({ "GET /decks": { body: [] } });
    renderApp("/");
    await userEvent.setup().click(await screen.findByRole("link", { name: "Account" }));
    expect(await screen.findByRole("heading", { name: "Account", level: 1 })).toBeInTheDocument();
    expect(document.title).toContain("Account");
    expect(panel("Email address")).toHaveTextContent("ada@example.com (confirmed)");
    expect(within(panel("Email address")).queryByRole("button")).toBeNull();
    expect(await screen.findByText(/You have used 37 of 200 pages/)).toHaveTextContent("163 pages are left");
    expect(screen.getByRole("meter", { name: "AI pages used this month" })).toHaveAttribute("value", "37");
    expect(await screen.findByText(/never uses your content to train/)).toBeInTheDocument();
    const terms = screen.getByRole("link", { name: "Read Anthropic's terms" });
    expect(terms).toHaveAttribute("href", "https://www.anthropic.com/legal/commercial-terms");
    expect(terms).toHaveAttribute("rel", "noopener noreferrer");
  });
});

describe("signed-in devices", () => {
  it("marks this device and signs out another one", async () => {
    let listed = [THIS_DEVICE, PHONE];
    const calls = accountApi({
      "GET /auth/sessions": () => ({ body: listed }),
      "DELETE /auth/sessions/s2": () => {
        listed = [THIS_DEVICE];
        return { status: 204 };
      },
    });
    await openAccount();
    const devices = panel("Signed-in devices");
    expect(await within(devices).findByText("(this device)")).toBeInTheDocument();
    expect(within(devices).getByText("Firefox on macOS")).toBeInTheDocument();
    // Only the other device has a sign-out button; this one signs out from the header.
    const buttons = within(devices).getAllByRole("button", { name: /^Sign out Safari on iPhone/ });
    expect(buttons).toHaveLength(1);
    await userEvent.setup().click(buttons[0] as HTMLElement);
    expect(await within(devices).findByRole("status")).toHaveTextContent("Signed out Safari on iPhone.");
    expect(within(devices).getByRole("heading", { name: "Signed-in devices" })).toHaveFocus();
    await waitFor(() => {
      expect(within(devices).queryByText("Safari on iPhone")).toBeNull();
    });
    expect(calls.filter((c) => c.path === "/auth/sessions")).toHaveLength(2);
    expect(within(devices).queryByRole("button", { name: "Sign out all other devices" })).toBeNull();
  });

  it("says when a device had already signed out", async () => {
    accountApi({ "DELETE /auth/sessions/s2": { status: 404, body: { detail: "No such session." } } });
    await openAccount();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /^Sign out Safari on iPhone/ }));
    expect(await within(panel("Signed-in devices")).findByRole("status")).toHaveTextContent(
      "That device was already signed out.",
    );
  });

  it("shows why a device could not be signed out", async () => {
    accountApi({ "DELETE /auth/sessions/s2": { status: 500 } });
    await openAccount();
    await userEvent.setup().click(await screen.findByRole("button", { name: /^Sign out Safari on iPhone/ }));
    expect(await within(panel("Signed-in devices")).findByRole("alert")).toHaveTextContent(
      "Something went wrong on the server.",
    );
  });

  it("signs out every other device after asking", async () => {
    const calls = accountApi({ "POST /auth/sessions/revoke-others": { body: { revoked: 1 } } });
    await openAccount();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Sign out all other devices" }));
    const confirm = screen.getByRole("alertdialog", { name: "Sign out all other devices" });
    expect(within(confirm).getByRole("button", { name: "Cancel" })).toHaveFocus();
    await user.click(within(confirm).getByRole("button", { name: "Sign out other devices" }));
    expect(await within(panel("Signed-in devices")).findByText("Signed out 1 other device.")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST" && c.path === "/auth/sessions/revoke-others")).toBe(true);
  });

  it("counts several devices and reports a failure", async () => {
    let fail = false;
    accountApi({
      "POST /auth/sessions/revoke-others": () => (fail ? { status: 500 } : { body: { revoked: 2 } }),
    });
    await openAccount();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Sign out all other devices" }));
    await user.click(screen.getByRole("button", { name: "Sign out other devices" }));
    expect(await screen.findByText("Signed out 2 other devices.")).toBeInTheDocument();
    fail = true;
    await user.click(await screen.findByRole("button", { name: "Sign out all other devices" }));
    await user.click(screen.getByRole("button", { name: "Sign out other devices" }));
    expect(await within(panel("Signed-in devices")).findByRole("alert")).toHaveTextContent(
      "Something went wrong on the server.",
    );
    // The earlier success message does not linger next to the failure.
    expect(screen.queryByText("Signed out 2 other devices.")).toBeNull();
  });

  it("shows when the device list cannot load", async () => {
    accountApi({ "GET /auth/sessions": { status: 500 } });
    await openAccount();
    expect(await within(panel("Signed-in devices")).findByRole("alert")).toHaveTextContent(
      "Something went wrong on the server.",
    );
  });
});

describe("AI quota", () => {
  it("says calmly when the month's pages are used up", async () => {
    accountApi({
      "GET /account/ai-quota": {
        body: { period: "2026-11", monthly_pages: 1, used_pages: 3, max_document_pages: 300, quota_applies: true },
      },
    });
    await openAccount();
    expect(await screen.findByText(/You have used all 1 page for/)).toHaveTextContent(
      "The allowance renews at the start of next month.",
    );
    expect(screen.getByRole("meter")).toHaveAttribute("value", "1");
  });

  it("says when AI generation is not included, with one page left otherwise", async () => {
    accountApi({
      "GET /account/ai-quota": {
        body: { period: "2026-11", monthly_pages: 0, used_pages: 0, max_document_pages: 300, quota_applies: true },
      },
    });
    await openAccount();
    expect(await screen.findByText(/AI generation is not included in your account/)).toBeInTheDocument();
    expect(screen.queryByRole("meter")).toBeNull();
  });

  it("uses the singular for one page left", async () => {
    accountApi({
      "GET /account/ai-quota": {
        body: { period: "2026-11", monthly_pages: 2, used_pages: 1, max_document_pages: 300, quota_applies: true },
      },
    });
    await openAccount();
    expect(await screen.findByText(/You have used 1 of 2 pages/)).toHaveTextContent("so 1 page are left");
  });

  it("tells an admin they have no monthly page limit", async () => {
    accountApi({
      "GET /account/ai-quota": {
        body: { period: "2026-11", monthly_pages: 10, used_pages: 42, max_document_pages: null, quota_applies: false },
      },
    });
    await openAccount();
    expect(await screen.findByText(/As an admin you have no monthly page limit/)).toHaveTextContent(
      "You have used 42 pages for",
    );
    expect(screen.queryByRole("meter")).toBeNull();
  });

  it("uses the singular for an admin's one page", async () => {
    accountApi({
      "GET /account/ai-quota": {
        body: { period: "2026-11", monthly_pages: 10, used_pages: 1, max_document_pages: null, quota_applies: false },
      },
    });
    await openAccount();
    expect(await screen.findByText(/You have used 1 page for/)).toBeInTheDocument();
  });

  it("shows when the quota cannot load", async () => {
    accountApi({ "GET /account/ai-quota": { status: 500 } });
    await openAccount();
    expect(await within(panel("AI pages this month")).findByRole("alert")).toBeInTheDocument();
  });

  it("names the month", () => {
    expect(formatPeriod("2026-11")).toMatch(/2026/);
    expect(formatPeriod("soon")).toBe("soon");
  });
});

describe("AI data-use disclosure", () => {
  it("shows no provider when AI is off", async () => {
    accountApi({
      "GET /legal/ai-disclosure": {
        body: {
          ai_enabled: false,
          trains_on_user_content: false,
          statements: ["AI features are off."],
          provider: null,
        },
      },
    });
    await openAccount();
    expect(await screen.findByText("AI features are off.")).toBeInTheDocument();
    expect(screen.queryByText(/AI provider on this server/)).toBeNull();
  });

  it("names a provider without a usable terms link", async () => {
    accountApi({
      "GET /legal/ai-disclosure": {
        body: { ...DISCLOSURE, provider: { name: "Local model", terms_url: "javascript:alert(1)" } },
      },
    });
    await openAccount();
    expect(await screen.findByText(/AI provider on this server: Local model/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /terms/ })).toBeNull();
  });

  it("shows when the disclosure cannot load", async () => {
    accountApi({ "GET /legal/ai-disclosure": { status: 500 } });
    await openAccount();
    expect(await within(panel("How AI features use your content")).findByRole("alert")).toBeInTheDocument();
  });

  it("accepts only web addresses as terms links", () => {
    expect(safeTermsUrl(null)).toBeNull();
    expect(safeTermsUrl("not a url")).toBeNull();
    expect(safeTermsUrl("data:text/html,x")).toBeNull();
    expect(safeTermsUrl("http://example.com/terms")).toBe("http://example.com/terms");
  });
});

describe("email confirmation", () => {
  const unverified = { ...USER, email_verified: false };

  it("sends the link again, then waits out the cooldown", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const calls = accountApi({
      "GET /auth/me": { body: unverified },
      "POST /auth/verify-email/resend": { status: 202 },
    });
    await openAccount();
    const email = panel("Email address");
    expect(email).toHaveTextContent("(not confirmed yet)");
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime.bind(vi) });
    const button = within(email).getByRole("button", { name: "Send the confirmation link again" });
    await user.click(button);
    expect(await within(email).findByRole("status")).toHaveTextContent("wait a minute before asking again");
    expect(button).toBeDisabled();
    expect(calls.filter((c) => c.path === "/auth/verify-email/resend")).toHaveLength(1);
    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    expect(button).toBeEnabled();
    expect(within(email).getByRole("status")).toBeEmptyDOMElement();
  });

  it("shows why the link could not be sent", async () => {
    accountApi({ "GET /auth/me": { body: unverified }, "POST /auth/verify-email/resend": { status: 500 } });
    await openAccount();
    await userEvent
      .setup()
      .click(within(panel("Email address")).getByRole("button", { name: "Send the confirmation link again" }));
    expect(await within(panel("Email address")).findByRole("alert")).toBeInTheDocument();
  });
});

describe("data export", () => {
  function stubDownloads(): { click: ReturnType<typeof vi.fn>; revoke: ReturnType<typeof vi.fn>; blobs: Blob[] } {
    const blobs: Blob[] = [];
    const revoke = vi.fn();
    vi.stubGlobal(
      "URL",
      Object.assign(URL, {
        createObjectURL: vi.fn((blob: Blob) => {
          blobs.push(blob);
          return "blob:export";
        }),
        revokeObjectURL: revoke,
      }),
    );
    const click = vi.fn();
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(click);
    return { click, revoke, blobs };
  }

  it("downloads the export as a JSON file", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const downloads = stubDownloads();
    let downloadName = "";
    downloads.click.mockImplementation(function (this: HTMLAnchorElement) {
      downloadName = this.download;
    });
    accountApi({ "GET /account/export": { body: { user: { id: "u1" }, decks: [] } } });
    await openAccount();
    await userEvent
      .setup({ advanceTimers: vi.advanceTimersByTime.bind(vi) })
      .click(screen.getByRole("button", { name: "Download my data" }));
    expect(await screen.findByText("Your data has been downloaded.")).toBeInTheDocument();
    expect(downloads.click).toHaveBeenCalledTimes(1);
    expect(downloadName).toMatch(/^flash-cards-export-\d{4}-\d{2}-\d{2}\.json$/);
    expect(JSON.parse(await (downloads.blobs[0] as Blob).text())).toEqual({ user: { id: "u1" }, decks: [] });
    // Kept long enough for the browser to start the download.
    expect(downloads.revoke).not.toHaveBeenCalled();
    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    expect(downloads.revoke).toHaveBeenCalledWith("blob:export");
    expect(document.querySelector("a[download]")).toBeNull();
  });

  it("shows an error instead of downloading it", async () => {
    const downloads = stubDownloads();
    accountApi({ "GET /account/export": { status: 500, body: { detail: "Export failed." } } });
    await openAccount();
    await userEvent.setup().click(screen.getByRole("button", { name: "Download my data" }));
    expect(await within(panel("Download your data")).findByRole("alert")).toHaveTextContent("Export failed.");
    expect(downloads.click).not.toHaveBeenCalled();
    expect(downloads.blobs).toEqual([]);
  });

  it("refuses to save an empty answer", async () => {
    const downloads = stubDownloads();
    accountApi({ "GET /account/export": { status: 200 } });
    await openAccount();
    await userEvent.setup().click(screen.getByRole("button", { name: "Download my data" }));
    expect(await within(panel("Download your data")).findByRole("alert")).toHaveTextContent("empty export");
    expect(downloads.click).not.toHaveBeenCalled();
  });
});

describe("account deletion", () => {
  function setOnline(value: boolean): void {
    Object.defineProperty(navigator, "onLine", { configurable: true, get: () => value });
  }

  async function openDialog(): Promise<ReturnType<typeof userEvent.setup>> {
    await openAccount();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Delete account…" }));
    return user;
  }

  it("needs the email typed, then deletes and forgets the account on this device", async () => {
    const remove = vi.fn(() => Promise.resolve(true));
    vi.stubGlobal("caches", { delete: remove });
    // Offline, so the queued answer stays queued until the account is deleted.
    setOnline(false);
    await enqueue({ idempotency_key: "k1", user_id: USER.id, card_id: "c1", deck_id: "d1", rating: 3, queued_at: 1 });
    await enqueue({ idempotency_key: "k2", user_id: "u2", card_id: "c2", deck_id: "d2", rating: 3, queued_at: 1 });
    rememberAiNoticeSeen(USER.id);
    const calls = accountApi({ "DELETE /account": { status: 204 } });
    const user = await openDialog();
    const dialog = screen.getByRole("dialog", { name: "Delete your account" });
    expect(dialog).toHaveFocus();
    const confirm = within(dialog).getByRole("button", { name: "Delete my account permanently" });
    expect(confirm).toBeDisabled();
    const input = within(dialog).getByLabelText("Type your email address to confirm");
    await user.type(input, "ada@example");
    expect(confirm).toBeDisabled();
    await user.type(input, ".COM ");
    expect(confirm).toBeEnabled();
    await user.click(confirm);
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Your account has been deleted.");
    expect(calls.filter((c) => c.method === "DELETE" && c.path === "/account")).toHaveLength(1);
    expect(calls.some((c) => c.path === "/auth/logout")).toBe(false);
    expect(await pending(USER.id)).toEqual([]);
    expect((await pending("u2")).map((r) => r.idempotency_key)).toEqual(["k2"]);
    expect(localStorage.getItem("flashcards.lastUser")).toBeNull();
    expect(aiNoticeSeen(USER.id)).toBe(false);
    expect(remove).toHaveBeenCalledWith("flashcards-study-data");
  });

  it("explains why the last admin cannot delete their account", async () => {
    accountApi({
      "GET /auth/me": { body: { ...USER, role: "admin" } },
      "DELETE /account": { status: 409, body: { detail: "At least one active admin must remain." } },
    });
    const user = await openDialog();
    await user.type(screen.getByLabelText("Type your email address to confirm"), USER.email);
    await user.click(screen.getByRole("button", { name: "Delete my account permanently" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "At least one active admin must remain. Make another person an admin first",
    );
    expect(screen.getByRole("dialog", { name: "Delete your account" })).toBeInTheDocument();
    expect(localStorage.getItem("flashcards.lastUser")).not.toBeNull();
  });

  it("keeps the account when the server cannot be reached", async () => {
    accountApi({ "DELETE /account": new TypeError("offline") });
    const user = await openDialog();
    await user.type(screen.getByLabelText("Type your email address to confirm"), USER.email);
    await user.click(screen.getByRole("button", { name: "Delete my account permanently" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not reach the server, so the account may not be deleted.",
    );
    expect(screen.getByRole("heading", { name: "Account", level: 1 })).toBeInTheDocument();
  });

  it("shows a server failure and keeps the account", async () => {
    accountApi({ "DELETE /account": { status: 500 } });
    const user = await openDialog();
    await user.type(screen.getByLabelText("Type your email address to confirm"), USER.email);
    await user.click(screen.getByRole("button", { name: "Delete my account permanently" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong on the server.");
    expect(localStorage.getItem("flashcards.lastUser")).not.toBeNull();
  });

  it("stays open, without Cancel, Close or Escape, while the request runs", async () => {
    let answer: (reply: { status: number; body?: unknown }) => void = () => undefined;
    accountApi({
      "DELETE /account": () =>
        new Promise((resolve) => {
          answer = resolve;
        }),
    });
    const user = await openDialog();
    await user.type(screen.getByLabelText("Type your email address to confirm"), USER.email);
    await user.click(screen.getByRole("button", { name: "Delete my account permanently" }));
    const dialog = screen.getByRole("dialog", { name: "Delete your account" });
    expect(within(dialog).getByRole("button", { name: "Deleting…" })).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toBeDisabled();
    await user.click(within(dialog).getByRole("button", { name: "Close" }));
    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(screen.getByRole("dialog", { name: "Delete your account" })).toBeInTheDocument();
    answer({ status: 409, body: { detail: "At least one active admin must remain." } });
    expect(await screen.findByRole("alert")).toHaveTextContent("At least one active admin must remain.");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("clears this device when the session is already gone, as after a lost answer", async () => {
    setOnline(false);
    await enqueue({ idempotency_key: "k1", user_id: USER.id, card_id: "c1", deck_id: "d1", rating: 3, queued_at: 1 });
    rememberAiNoticeSeen(USER.id);
    accountApi({ "DELETE /account": { status: 401, body: { detail: "Not authenticated." } } });
    const user = await openDialog();
    await user.type(screen.getByLabelText("Type your email address to confirm"), USER.email);
    await user.click(screen.getByRole("button", { name: "Delete my account permanently" }));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    await waitFor(async () => {
      expect(await pending(USER.id)).toEqual([]);
    });
    expect(aiNoticeSeen(USER.id)).toBe(false);
    expect(localStorage.getItem("flashcards.lastUser")).toBeNull();
  });

  it("still signs out when this browser has no offline storage", async () => {
    accountApi({ "DELETE /account": { status: 204 } });
    const user = await openDialog();
    await user.type(screen.getByLabelText("Type your email address to confirm"), USER.email);
    await resetForTests();
    vi.stubGlobal("indexedDB", undefined);
    await user.click(screen.getByRole("button", { name: "Delete my account permanently" }));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });

  it("ignores Enter before the email matches, and closes with Escape or Cancel", async () => {
    const calls = accountApi();
    const user = await openDialog();
    const opener = screen.getByRole("button", { name: "Delete account…" });
    const input = screen.getByLabelText("Type your email address to confirm");
    await user.type(input, "nope{Enter}");
    fireEvent.submit(input.closest("form") as HTMLFormElement);
    expect(calls.some((c) => c.path === "/account")).toBe(false);
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(opener).toHaveFocus();
    await user.click(opener);
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("AI notice on the generate page", () => {
  const generateRoutes = (disclosure: Routes[string]): Routes => ({
    "GET /auth/me": { body: USER },
    "GET /legal/ai-disclosure": disclosure,
  });

  function notice(): HTMLElement {
    return screen.getByRole("complementary", { name: "How your files are used" });
  }

  it("names the provider before the first upload and stays dismissed for this user", async () => {
    mockApi(generateRoutes({ body: DISCLOSURE }));
    const first = renderApp("/decks/d1/generate");
    expect(await screen.findByText(/sent to Anthropic to draft cards/)).toHaveTextContent(
      "Your content is never used to train AI models.",
    );
    await userEvent.setup().click(within(notice()).getByRole("button", { name: "Got it" }));
    expect(screen.queryByRole("complementary")).toBeNull();
    expect(aiNoticeSeen(USER.id)).toBe(true);
    expect(aiNoticeSeen("someone-else")).toBe(false);
    first.unmount();
    renderApp("/decks/d1/generate");
    expect(await screen.findByRole("heading", { name: "Generate cards from a file" })).toBeInTheDocument();
    expect(screen.queryByRole("complementary")).toBeNull();
  });

  it("links to the full notice on the account page and scrolls to it", async () => {
    const scroll = vi.fn();
    Element.prototype.scrollIntoView = scroll;
    accountApi();
    renderApp("/decks/d1/generate");
    await userEvent.setup().click(await screen.findByRole("link", { name: "Read the full AI data-use notice" }));
    expect(await screen.findByRole("heading", { name: "Account", level: 1 })).toBeInTheDocument();
    await waitFor(() => {
      expect(scroll).toHaveBeenCalledTimes(1);
    });
    expect(scroll.mock.contexts[0]).toBe(document.getElementById("ai-data"));
  });

  it("says something useful while or when the disclosure cannot load", async () => {
    mockApi(generateRoutes({ status: 500 }));
    renderApp("/decks/d1/generate");
    expect(await screen.findByText(/sent to this server's AI provider/)).toBeInTheDocument();
  });

  it("repeats the server's statements when AI is off, and warns when content may be used for training", async () => {
    mockApi(
      generateRoutes({
        body: {
          ai_enabled: false,
          trains_on_user_content: false,
          statements: ["AI is off.", "Nothing is sent."],
          provider: null,
        },
      }),
    );
    const view = renderApp("/decks/d1/generate");
    expect(await screen.findByText("AI is off. Nothing is sent.")).toBeInTheDocument();
    view.unmount();
    mockApi(generateRoutes({ body: { ...DISCLOSURE, trains_on_user_content: true } }));
    renderApp("/decks/d1/generate");
    const text = await screen.findByText(/sent to Anthropic to draft cards/);
    expect(text).not.toHaveTextContent("never used to train");
    expect(text).toHaveTextContent("Your content may be used to train AI models");
  });

  it("still shows and hides when storage is blocked", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    mockApi(generateRoutes({ body: DISCLOSURE }));
    renderApp("/decks/d1/generate");
    await userEvent.setup().click(await screen.findByRole("button", { name: "Got it" }));
    expect(screen.queryByRole("complementary")).toBeNull();
    expect(aiNoticeSeen(USER.id)).toBe(false);
    expect(() => {
      forgetAiNotice(USER.id);
    }).not.toThrow();
  });
});
