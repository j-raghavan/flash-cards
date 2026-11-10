// SPDX-License-Identifier: AGPL-3.0-only
// The shared admin library (ADR 0024): an admin's deck is in every member's
// library, read-only, and each member studies it with their own progress.
//
// Every member sees an active admin's decks, so this spec runs in its own
// project after the rest of the suite (see playwright.config.ts): a library deck
// would otherwise reach the other tests' fresh members and change their home page.
import { uniqueName } from "../support/api";
import { expect, test } from "../support/fixtures";

test("a member studies an admin's deck from the library, read-only, with their own progress", async ({
  page,
  member,
  libraryAdmin,
}) => {
  const admin = libraryAdmin.api;
  const deck = await libraryAdmin.createDeck(uniqueName("Library anatomy"));
  await admin.addBasicCard(deck.id, "Longest bone in the body?", "The femur");

  // The deck is listed under Library, marked, and not among the member's own decks.
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Decks", level: 1 })).toBeVisible();
  const library = page.getByRole("region", { name: "Library" });
  await expect(library.getByText("Shared by your admin. Study them; your progress is your own.")).toBeVisible();
  const tile = library.locator("li.deck-tile").filter({ hasText: deck.name });
  await expect(tile.getByText("Library", { exact: true })).toBeVisible();
  await expect(page.getByText(/No decks of your own yet/)).toBeVisible();

  // Its page is for study only.
  await tile.getByRole("link", { name: deck.name }).click();
  await expect(page.getByRole("heading", { name: deck.name, level: 1 })).toBeVisible();
  await expect(page.getByText("From the library")).toBeVisible();
  for (const name of ["Add card", "Generate from a file", "Browse cards", "Mock exam", "Share"]) {
    await expect(page.getByRole("link", { name })).toHaveCount(0);
  }
  for (const name of ["Rename", "Delete deck"]) {
    await expect(page.getByRole("button", { name })).toHaveCount(0);
  }

  // Studying it: reveal and rate, with no way to edit the card.
  await page.getByRole("link", { name: "Study now" }).click();
  await expect(page.getByText("Longest bone in the body?")).toBeVisible();
  await expect(page.getByRole("button", { name: /Edit card/ })).toHaveCount(0);
  await expect(page.getByLabel("Typing it (AI checks)")).toHaveCount(0);
  await page.getByRole("button", { name: /Show answer/ }).click();
  await expect(page.getByText("The femur")).toBeVisible();
  await page.getByRole("button", { name: /Good/ }).click();
  await expect(page.getByRole("heading", { name: "All caught up" })).toBeVisible();
  await expect(page.getByText("You reviewed 1 card.")).toBeVisible();

  // The answer is the member's own: the admin's card is still new to the admin.
  await expect.poll(async () => (await member.api.deckStats(deck.id)).new).toBe(0);
  const own = await admin.deckStats(deck.id);
  expect(own).toMatchObject({ total: 1, new: 1, learning: 0, review: 0 });
});
