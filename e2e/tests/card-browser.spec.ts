// SPDX-License-Identifier: AGPL-3.0-only
// The card browser (F-CARD-5, ADR 0011): filter by tag, then suspend the matches
// with one bulk action. Browsing is the admin's, so the page signs in as one.
import { uniqueName } from "../support/api";
import { expect, test } from "../support/fixtures";

test("filters the card browser by tag and suspends the matches in bulk", async ({ page, admin }) => {
  const deck = await admin.createDeck(uniqueName("Chemistry"));
  await admin.api.addBasicCard(deck.id, "Symbol for sodium?", "Na", ["elements"]);
  await admin.api.addBasicCard(deck.id, "Symbol for potassium?", "K", ["elements"]);
  await admin.api.addBasicCard(deck.id, "pH of pure water?", "7", ["acids"]);

  await page.goto("/cards");
  await expect(page.getByRole("heading", { name: "Browse cards" })).toBeVisible();
  const rows = page.locator("table.browser tbody tr");
  await expect(rows).toHaveCount(3);

  const filters = page.getByRole("form", { name: "Filters" });
  await filters.getByLabel("Tag").fill("elements");
  await filters.getByRole("button", { name: "Search" }).click();
  await expect(page).toHaveURL(/tag=elements/);
  await expect(rows).toHaveCount(2);
  await expect(rows.filter({ hasText: "pH of pure water?" })).toHaveCount(0);

  await page.getByRole("checkbox", { name: "Select all on this page" }).check();
  const bulk = page.getByRole("form", { name: "Bulk actions" });
  await bulk.getByLabel("Action").selectOption({ label: "Suspend" });
  await bulk.getByRole("button", { name: "Apply" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Updated 2 cards." })).toBeVisible();
  await expect(rows.locator("td:nth-child(4)")).toHaveText(["suspended", "suspended"]);

  // The card outside the filter is untouched.
  await filters.getByLabel("Tag").fill("acids");
  await filters.getByRole("button", { name: "Search" }).click();
  await expect(rows).toHaveCount(1);
  await expect(rows.locator("td:nth-child(4)")).toHaveText(["new"]);
});
