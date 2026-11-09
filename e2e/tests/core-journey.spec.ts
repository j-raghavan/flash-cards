// SPDX-License-Identifier: AGPL-3.0-only
// The journey the PRD names (section 8): invite -> sign-up -> deck and card ->
// upload -> review drafts -> study -> share, then a second user clones the deck.
import { Api, createInvite, PASSWORD, signUpWithApi, uniqueEmail } from "../support/api";
import { expect, test } from "../support/fixtures";
import { skipGuidedSetup } from "../support/onboarding";
import { textPdf } from "../support/pdf";

// Five pages, two fixed drafts each from the fake AI provider, which takes a
// second per page: the job runs long enough for its progress to be watched.
const PAGES = [
  "Cell biology: the mitochondrion makes ATP.",
  "Thermodynamics: water boils at 100 degrees.",
  "Revision notes for the end-of-term exam.",
  "Genetics: DNA is copied before a cell divides.",
  "Ecology: energy flows from producers to consumers.",
];
const DRAFTS = PAGES.length * 2;

test("invite, sign up, build a deck with AI, study it, share it and clone it", async ({ page, browser }) => {
  test.setTimeout(90_000);
  const email = uniqueEmail("journey");
  const deckName = `Biology ${email.slice(4, 16)}`;
  const api = new Api(page.context().request);

  await test.step("an admin invites a member, who signs up in the browser", async () => {
    const token = await createInvite();
    await page.goto(`/signup#token=${encodeURIComponent(token)}`);
    await expect(page.getByRole("heading", { name: "Create your account" })).toBeVisible();
    // The token leaves the address bar as soon as the page has read it.
    await expect(page).toHaveURL(/\/signup$/);
    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Create account" }).click();
    // A new member with no decks is offered the guided setup first; it is skippable.
    await skipGuidedSetup(page);
  });

  await test.step("creates a deck and adds a card by hand", async () => {
    await page.getByLabel("New deck").fill(deckName);
    await page.getByRole("button", { name: "Add deck" }).click();
    await page.getByRole("link", { name: deckName }).click();
    await expect(page.getByRole("heading", { name: deckName, level: 1 })).toBeVisible();

    await page.getByRole("link", { name: "Add card" }).click();
    await page.getByLabel("Front").fill("What is the basic unit of life?");
    await page.getByLabel("Back").fill("The cell");
    await page.getByRole("button", { name: "Add card" }).click();
    await expect(page.getByRole("status").filter({ hasText: "Added 1 card." })).toBeVisible();
    await page.getByRole("link", { name: "Back to deck" }).click();
  });

  await test.step("uploads a PDF and watches the generation progress live", async () => {
    await page.getByRole("link", { name: "Generate from a file" }).click();
    await expect(page.getByRole("heading", { name: "How your files are used" })).toBeVisible();
    await page.getByRole("button", { name: "Got it" }).click();
    await page
      .getByLabel(/^File \(PDF/)
      .setInputFiles({ name: "notes.pdf", mimeType: "application/pdf", buffer: textPdf(PAGES) });

    // The job page subscribes to the job's Server-Sent Events stream. A 200 (not
    // the 204 sent for a finished job) means it subscribed while the job ran.
    const stream = page.waitForResponse(
      (response) =>
        /\/api\/ai\/jobs\/[^/]+\/events$/.test(new URL(response.url()).pathname) && response.status() === 200,
    );
    await page.getByRole("button", { name: "Generate drafts" }).click();
    await expect(page).toHaveURL(/\/jobs\/[^/?]+\?deck=/);
    expect((await stream).headers()["content-type"]).toContain("text/event-stream");
    await expect(page.getByRole("progressbar", { name: "Generation progress" })).toBeVisible();
    // The API runs one generation job at a time, so with tests repeated in parallel
    // this job can wait in the queue first.
    await expect(page.getByText(new RegExp(`\\d of ${String(PAGES.length)} pages`))).toBeVisible({
      timeout: 45_000,
    });
    await expect(page.getByRole("status").filter({ hasText: "Finished." })).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole("progressbar")).toHaveCount(0);
  });

  await test.step("reviews the AI drafts and approves them", async () => {
    await expect(page.getByRole("heading", { name: "Review the drafts" })).toBeVisible();
    await expect(page.getByRole("checkbox")).toHaveCount(DRAFTS);
    await page.getByRole("button", { name: `Approve ${String(DRAFTS)} cards` }).click();
    await expect(page.getByText(`Added ${String(DRAFTS)} cards. All drafts are reviewed.`)).toBeVisible();
    await page.getByRole("link", { name: "Go to the deck" }).click();
    await expect(page.getByRole("heading", { name: deckName, level: 1 })).toBeVisible();
  });

  const total = DRAFTS + 1;
  await test.step("studies with the keyboard and undoes an answer", async () => {
    await page.getByRole("link", { name: "Study now" }).click();
    const card = page.locator("article.study-card");
    await expect(page.getByText(`Card 1 of ${String(total)}`)).toBeVisible();
    await expect(card.locator(".front")).not.toBeEmpty();
    const firstFront = await card.locator(".front").innerText();

    await page.keyboard.press("Space");
    await expect(page.getByRole("group", { name: "How well did you know it?" })).toBeVisible();
    const answered = page.waitForResponse((r) => r.url().endsWith("/review") && r.request().method() === "POST");
    await page.keyboard.press("3");
    expect((await answered).ok()).toBe(true);
    await expect(page.getByText(`Card 2 of ${String(total)}`)).toBeVisible();
    await expect.poll(async () => (await api.exportAccount()).review_logs.length).toBe(1);

    // U takes the answer back: the first card is asked again and the review is gone.
    await expect(page.getByRole("button", { name: /Undo last answer/ })).toBeVisible();
    const undone = page.waitForResponse((r) => r.url().endsWith("/undo") && r.request().method() === "POST");
    await page.keyboard.press("u");
    expect((await undone).ok()).toBe(true);
    await expect(page.getByText(`Card 1 of ${String(total)}`)).toBeVisible();
    await expect(card.locator(".front")).toHaveText(firstFront);
    await expect(page.getByRole("button", { name: /Undo last answer/ })).toHaveCount(0);
    expect((await api.exportAccount()).review_logs).toHaveLength(0);

    // Answer it again, Easy this time.
    await page.keyboard.press("Space");
    await expect(page.getByRole("group", { name: "How well did you know it?" })).toBeVisible();
    await page.keyboard.press("4");
    await expect(page.getByText(`Card 2 of ${String(total)}`)).toBeVisible();
    await expect.poll(async () => (await api.exportAccount()).review_logs.map((log) => log.rating)).toEqual([4]);
  });

  let shareLink = "";
  await test.step("shares the deck with copying allowed", async () => {
    await page.getByRole("link", { name: "Back to deck" }).first().click();
    await page.getByRole("link", { name: "Share", exact: true }).click();
    await page.getByLabel("Allow people with the link to copy the deck into their own decks").check();
    await page.getByRole("button", { name: "Create link" }).click();
    const field = page.getByRole("textbox", { name: "Share link" });
    await expect(field).toHaveValue(/\/shared#token=/);
    shareLink = await field.inputValue();
  });

  await test.step("a second user previews the shared deck and copies it", async () => {
    const context = await browser.newContext();
    try {
      const other = await signUpWithApi(context.request, uniqueEmail("clone"));
      const otherPage = await context.newPage();
      await otherPage.goto(shareLink);
      await expect(otherPage.getByRole("heading", { name: deckName, level: 1 })).toBeVisible();
      await expect(otherPage.getByText("What is the basic unit of life?")).toBeVisible();
      await otherPage.getByRole("button", { name: "Copy to my decks" }).click();
      await expect(otherPage).toHaveURL(/\/decks\/[^/]+$/);
      await expect(otherPage.getByRole("heading", { name: deckName, level: 1 })).toBeVisible();

      const copied = await other.exportAccount();
      expect(copied.decks.map((d) => d.name)).toEqual([deckName]);
      expect(copied.cards).toHaveLength(total);
      // The copy is the second user's own: none of the owner's study history comes along.
      expect(copied.review_logs).toHaveLength(0);
    } finally {
      await context.close();
    }
  });
});
