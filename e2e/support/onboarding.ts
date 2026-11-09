// SPDX-License-Identifier: AGPL-3.0-only
// The guided first run (F-ONB-1, ADR 0020) opens once for a user with no decks
// who lands on the home page. Journeys that are not about it skip it, which also
// checks that it can be skipped.
import { expect, type Page } from "@playwright/test";

export async function skipGuidedSetup(page: Page): Promise<void> {
  await expect(page.getByRole("heading", { name: "Getting started", level: 1 })).toBeVisible();
  await page.getByRole("button", { name: "Skip the setup" }).click();
  await expect(page.getByRole("heading", { name: "Decks", level: 1 })).toBeVisible();
}
