// SPDX-License-Identifier: AGPL-3.0-only
// Offline review (F-STUDY-5): an answer given without a connection is kept on the
// device and sent once the connection returns, and recorded exactly once.
import { expect, test } from "../support/fixtures";

test("an answer given offline syncs once when the connection returns", async ({ page, context, member }) => {
  const deck = await member.api.createDeck("Offline deck");
  await member.api.addBasicCard(deck.id, "Capital of France?", "Paris");

  await page.goto(`/decks/${deck.id}/study`);
  await expect(page.getByText("Card 1 of 1")).toBeVisible();
  await expect(page.locator("article.study-card .front")).toHaveText("Capital of France?");

  await context.setOffline(true);
  const banner = page.getByRole("status").filter({ hasText: /offline|waiting to sync|Back online/ });
  await expect(banner).toContainText("You are offline.");

  await page.keyboard.press("Space");
  await expect(page.locator("article.study-card .back")).toHaveText("Paris");
  await page.keyboard.press("3");
  await expect(page.getByRole("heading", { name: "All caught up" })).toBeVisible();
  await expect(page.getByText("1 will sync when you are back online.")).toBeVisible();
  await expect(banner).toContainText("1 answer is waiting to sync.");
  // The API helper's requests are sent from Node, outside the browser's offline
  // emulation, so it still reaches the server.
  expect((await member.api.exportAccount()).review_logs).toHaveLength(0);

  const synced = page.waitForResponse(
    (r) => r.url().endsWith("/review") && r.request().method() === "POST" && r.ok(),
  );
  await context.setOffline(false);
  await synced;
  await expect(banner).toHaveCount(0);
  const afterSync = await member.api.exportAccount();
  expect(afterSync.review_logs).toHaveLength(1);
  expect(afterSync.review_logs[0]?.rating).toBe(3);

  // Opening the app again replays nothing: the queue is empty and the review stands once.
  await page.reload();
  await expect(page.getByRole("heading", { name: "All caught up" })).toBeVisible();
  await expect(banner).toHaveCount(0);
  expect((await member.api.exportAccount()).review_logs).toHaveLength(1);
});
