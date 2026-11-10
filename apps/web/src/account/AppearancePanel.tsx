// SPDX-License-Identifier: AGPL-3.0-only
// Light or dark, and the accent palette. Saved on the account, so it follows the
// user to every device; a change shows at once.

import { ACCENTS, THEMES } from "../lib/appearance";
import { useAppearance } from "../lib/useAppearance";
import { ErrorText } from "../components/ui";

export function AppearancePanel() {
  const { appearance, change, error } = useAppearance();
  return (
    <section className="panel" aria-labelledby="appearance-heading">
      <h2 id="appearance-heading">Appearance</h2>
      <p className="muted">Saved with your account, so it follows you to every device.</p>
      <div className="stack">
        <fieldset>
          <legend>Theme</legend>
          <div className="chips">
            {THEMES.map(({ value, label }) => (
              <label key={value}>
                <input
                  type="radio"
                  name="theme"
                  value={value}
                  checked={appearance.theme === value}
                  onChange={() => {
                    void change({ theme: value });
                  }}
                />
                {label}
              </label>
            ))}
          </div>
        </fieldset>
        <fieldset>
          <legend>Colour</legend>
          <div className="chips">
            {ACCENTS.map(({ value, label }) => (
              <label key={value}>
                <input
                  type="radio"
                  name="accent"
                  value={value}
                  checked={appearance.accent === value}
                  onChange={() => {
                    void change({ accent: value });
                  }}
                />
                <span className={`swatch swatch-${value}`} aria-hidden="true" />
                {label}
              </label>
            ))}
          </div>
        </fieldset>
      </div>
      <ErrorText message={error} />
    </section>
  );
}
