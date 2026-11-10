// SPDX-License-Identifier: AGPL-3.0-only
// How the app looks: light or dark, and an accent palette. The choice is saved on
// the account (PATCH /account/appearance) and arrives with the user; this module
// puts it on <html> (data-theme, data-accent, read by styles.css) and keeps the
// last one on this device, so the sign-in page and the first paint after a
// reload are already in it.

export type Theme = "system" | "light" | "dark";
export type Accent = "teal" | "indigo" | "rose" | "amber";

export interface Appearance {
  theme: Theme;
  accent: Accent;
}

export const THEMES: { value: Theme; label: string }[] = [
  { value: "system", label: "Match this device" },
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
];

export const ACCENTS: { value: Accent; label: string }[] = [
  { value: "teal", label: "Teal" },
  { value: "indigo", label: "Indigo" },
  { value: "rose", label: "Rose" },
  { value: "amber", label: "Amber" },
];

export const DEFAULT_APPEARANCE: Appearance = { theme: "system", accent: "teal" };

const STORAGE_KEY = "flashcards.appearance";

function isTheme(value: unknown): value is Theme {
  return THEMES.some((theme) => theme.value === value);
}

function isAccent(value: unknown): value is Accent {
  return ACCENTS.some((accent) => accent.value === value);
}

/** The look a payload asks for; anything missing or unknown keeps the default. */
export function appearanceOf(source: { theme?: unknown; accent?: unknown } | null): Appearance {
  return {
    theme: isTheme(source?.theme) ? source.theme : DEFAULT_APPEARANCE.theme,
    accent: isAccent(source?.accent) ? source.accent : DEFAULT_APPEARANCE.accent,
  };
}

/** Paint the page in this look and remember it on this device. */
export function applyAppearance(appearance: Appearance): void {
  const root = document.documentElement;
  root.dataset.theme = appearance.theme;
  root.dataset.accent = appearance.accent;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(appearance));
  } catch {
    // Storage can be unavailable (private mode); the look then lasts this visit.
  }
}

/** The look last used on this device, put back before the app first paints. */
export function restoreAppearance(): void {
  applyAppearance(appearanceOf(savedAppearance()));
}

function savedAppearance(): { theme?: unknown; accent?: unknown } | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const saved: unknown = raw === null ? null : JSON.parse(raw);
    return typeof saved === "object" ? saved : null;
  } catch {
    return null;
  }
}

/** Whether the page is dark right now: picked, or following a dark device. */
export function isDark(theme: Theme): boolean {
  if (theme !== "system") {
    return theme === "dark";
  }
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-color-scheme: dark)").matches;
}
