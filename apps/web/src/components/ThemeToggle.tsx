// SPDX-License-Identifier: AGPL-3.0-only
// The header's quick switch between light and dark. It picks the other one of
// what the page shows now, which follows the device live when the user has not
// picked; the full choice (and the colour) is on the Account page.

import { useSyncExternalStore } from "react";

import { deviceIsDark, isDark, onDeviceThemeChange } from "../lib/appearance";
import type { AppearanceChoice } from "../lib/useAppearance";

function Sun() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <circle cx="12" cy="12" r="4.5" fill="currentColor" />
      <g stroke="currentColor" strokeWidth="2" strokeLinecap="round">
        <path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
      </g>
    </svg>
  );
}

function Moon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path fill="currentColor" d="M20.5 14.6A8.5 8.5 0 0 1 9.4 3.5a8.5 8.5 0 1 0 11.1 11.1Z" />
    </svg>
  );
}

export function ThemeToggle({ choice }: { choice: AppearanceChoice }) {
  const deviceDark = useSyncExternalStore(onDeviceThemeChange, deviceIsDark);
  const dark = isDark(choice.appearance.theme, deviceDark);
  const label = dark ? "Switch to light theme" : "Switch to dark theme";
  return (
    <button
      type="button"
      className="icon-button"
      aria-label={label}
      title={label}
      onClick={() => {
        void choice.change({ theme: dark ? "light" : "dark" });
      }}
    >
      {dark ? <Sun /> : <Moon />}
    </button>
  );
}
