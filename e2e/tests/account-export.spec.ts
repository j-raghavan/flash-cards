// SPDX-License-Identifier: AGPL-3.0-only
// Data export from the account page (F-ACCT-2): a JSON file with the user's data.
import { readFile } from "node:fs/promises";

import type { AccountExport } from "../support/api";
import { expect, test } from "../support/fixtures";

test("downloads the account's data as JSON from the account page", async ({ page, member }) => {
  const deck = await member.api.createDeck("Exported deck");
  await member.api.addBasicCard(deck.id, "Exported question", "Exported answer");

  await page.goto("/account");
  await expect(page.getByRole("heading", { name: "Download your data" })).toBeVisible();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download my data" }).click();
  const file = await download;
  expect(file.suggestedFilename()).toMatch(/^flash-cards-export-\d{4}-\d{2}-\d{2}\.json$/);
  await expect(page.getByText("Your data has been downloaded.")).toBeVisible();

  const exported = JSON.parse(await readFile(await file.path(), "utf8")) as AccountExport;
  expect(exported.user.email).toBe(member.email);
  expect(exported.decks.map((d) => d.name)).toEqual(["Exported deck"]);
  expect(exported.cards).toHaveLength(1);
});
