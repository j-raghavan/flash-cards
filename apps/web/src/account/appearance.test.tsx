// SPDX-License-Identifier: AGPL-3.0-only
import { act, renderHook, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "../auth/AuthContext";
import { appearanceOf, applyAppearance, isDark, restoreAppearance } from "../lib/appearance";
import { useAppearance } from "../lib/useAppearance";
import { USER, mockApi, renderApp } from "../test/helpers";

const KEY = "flashcards.appearance";
const root = document.documentElement;

function deviceIsDark(dark: boolean): void {
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({ matches: dark && query === "(prefers-color-scheme: dark)" })),
  );
}

afterEach(() => {
  delete root.dataset.theme;
  delete root.dataset.accent;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const accountRoutes = {
  "GET /auth/sessions": { body: [] },
  "GET /account/ai-quota": {
    body: { period: "2026-11", monthly_pages: 200, used_pages: 0, max_document_pages: 300, quota_applies: true },
  },
  "GET /legal/ai-disclosure": { body: { ai_enabled: false, trains_on_user_content: false, statements: [] } },
  "GET /auth/two-factor": {
    body: { enabled: false, totp: false, totp_available: true, passkeys: [], recovery_codes_left: 0 },
  },
};

describe("appearance helpers", () => {
  it("reads a look from a payload, keeping the default for anything missing or unknown", () => {
    expect(appearanceOf(null)).toEqual({ theme: "system", accent: "teal" });
    expect(appearanceOf({})).toEqual({ theme: "system", accent: "teal" });
    expect(appearanceOf({ theme: "dark", accent: "rose" })).toEqual({ theme: "dark", accent: "rose" });
    expect(appearanceOf({ theme: "sepia", accent: 3 })).toEqual({ theme: "system", accent: "teal" });
  });

  it("paints the page and remembers the look on this device", () => {
    applyAppearance({ theme: "light", accent: "amber" });
    expect(root.dataset.theme).toBe("light");
    expect(root.dataset.accent).toBe("amber");
    expect(JSON.parse(localStorage.getItem(KEY) ?? "null")).toEqual({ theme: "light", accent: "amber" });
  });

  it("still paints the page when storage is unavailable", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("private mode");
    });
    applyAppearance({ theme: "dark", accent: "indigo" });
    expect(root.dataset.theme).toBe("dark");
  });

  it("puts back the look last used here, or the default when there is none or it is unreadable", () => {
    localStorage.setItem(KEY, JSON.stringify({ theme: "dark", accent: "indigo" }));
    restoreAppearance();
    expect([root.dataset.theme, root.dataset.accent]).toEqual(["dark", "indigo"]);
    for (const stored of [null, "not json", '"dark"', "null"]) {
      if (stored === null) {
        localStorage.removeItem(KEY);
      } else {
        localStorage.setItem(KEY, stored);
      }
      restoreAppearance();
      expect([root.dataset.theme, root.dataset.accent]).toEqual(["system", "teal"]);
    }
  });

  it("knows whether the page is dark, following the device when asked to", () => {
    expect(isDark("dark")).toBe(true);
    expect(isDark("light")).toBe(false);
    deviceIsDark(true);
    expect(isDark("system")).toBe(true);
    deviceIsDark(false);
    expect(isDark("system")).toBe(false);
    vi.stubGlobal("matchMedia", undefined);
    expect(isDark("system")).toBe(false);
  });
});

describe("appearance choice", () => {
  it("paints the page in the signed-in user's look", async () => {
    mockApi({ "GET /auth/me": { body: { ...USER, theme: "dark", accent: "rose" } }, "GET /decks": { body: [] } });
    renderApp("/");
    await waitFor(() => {
      expect([root.dataset.theme, root.dataset.accent]).toEqual(["dark", "rose"]);
    });
  });

  it("changes the theme and colour from the Account page, showing them at once and saving them", async () => {
    const calls = mockApi({
      ...accountRoutes,
      "GET /auth/me": { body: USER },
      "PATCH /account/appearance": (call) => ({
        body: { theme: "system", accent: "teal", ...(call.body as object) },
      }),
    });
    renderApp("/account");
    const panel = await screen.findByRole("region", { name: "Appearance" });
    const theme = within(panel).getByRole("group", { name: "Theme" });
    expect(within(theme).getByRole("radio", { name: "Match this device" })).toBeChecked();
    const user = userEvent.setup();

    await user.click(within(theme).getByRole("radio", { name: "Dark" }));
    expect(root.dataset.theme).toBe("dark");
    await waitFor(() => {
      expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ theme: "dark" });
    });
    expect(within(theme).getByRole("radio", { name: "Dark" })).toBeChecked();

    const colour = within(panel).getByRole("group", { name: "Colour" });
    await user.click(within(colour).getByRole("radio", { name: "Indigo" }));
    expect(root.dataset.accent).toBe("indigo");
    await waitFor(() => {
      expect(calls.filter((c) => c.method === "PATCH").at(-1)?.body).toEqual({ accent: "indigo" });
    });
  });

  it("brings the previous look back and says why when saving fails", async () => {
    mockApi({
      ...accountRoutes,
      "GET /auth/me": { body: { ...USER, theme: "light", accent: "teal" } },
      "PATCH /account/appearance": { status: 500, body: { detail: "Could not save." } },
    });
    renderApp("/account");
    const panel = await screen.findByRole("region", { name: "Appearance" });
    await userEvent.setup().click(within(panel).getByRole("radio", { name: "Rose" }));
    expect(await within(panel).findByRole("alert")).toHaveTextContent("Could not save.");
    expect(root.dataset.accent).toBe("teal");
    expect(within(panel).getByRole("radio", { name: "Teal" })).toBeChecked();
  });

  it("switches between light and dark from the header", async () => {
    deviceIsDark(true);
    const calls = mockApi({
      "GET /auth/me": { body: USER },
      "GET /decks": { body: [] },
      "PATCH /account/appearance": (call) => ({ body: { theme: "system", accent: "teal", ...(call.body as object) } }),
    });
    renderApp("/");
    // Following a dark device, the switch offers light.
    const toLight = await screen.findByRole("button", { name: "Switch to light theme" });
    const user = userEvent.setup();
    await user.click(toLight);
    expect(root.dataset.theme).toBe("light");
    expect(await screen.findByRole("button", { name: "Switch to dark theme" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Switch to dark theme" }));
    expect(root.dataset.theme).toBe("dark");
    await waitFor(() => {
      expect(calls.filter((c) => c.method === "PATCH").map((c) => c.body)).toEqual([
        { theme: "light" },
        { theme: "dark" },
      ]);
    });
  });

  it("does nothing without a signed-in user", async () => {
    const calls = mockApi({ "GET /auth/me": { status: 401, body: { detail: "Not signed in." } } });
    const wrapper = ({ children }: { children: ReactNode }) => <AuthProvider>{children}</AuthProvider>;
    const { result } = renderHook(() => useAppearance(), { wrapper });
    await waitFor(() => {
      expect(calls.some((c) => c.path === "/auth/me")).toBe(true);
    });
    await act(async () => {
      await result.current.change({ theme: "dark" });
    });
    expect(calls.some((c) => c.method === "PATCH")).toBe(false);
    expect(result.current.appearance).toEqual({ theme: "system", accent: "teal" });
  });
});
