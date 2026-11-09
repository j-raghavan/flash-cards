// SPDX-License-Identifier: AGPL-3.0-only
// Password reset through the emailed link (F-AUTH-2). The development mailer
// writes each email to the API log, which stands in for the inbox.
import { request } from "@playwright/test";

import { linkFromEmail, PASSWORD, signUpWithApi, uniqueEmail } from "../support/api";
import { WEB_URL } from "../support/env";
import { expect, test } from "../support/fixtures";
import { skipGuidedSetup } from "../support/onboarding";

const NEW_PASSWORD = "e2e a brand new passphrase";

test("a member resets a forgotten password from the emailed link", async ({ page }) => {
  const email = uniqueEmail("reset");
  // The account is made outside the browser, which stays signed out.
  const setup = await request.newContext({ baseURL: WEB_URL });
  try {
    await signUpWithApi(setup, email);
  } finally {
    await setup.dispose();
  }

  await page.goto("/login");
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await page.getByRole("link", { name: "Forgot your password?" }).click();
  // Both pages have an Email field: wait for the new page before typing.
  await expect(page.getByRole("heading", { name: "Reset your password" })).toBeVisible();
  await page.getByLabel("Email").fill(email);
  await page.getByRole("button", { name: "Send reset link" }).click();
  await expect(page.getByRole("status").filter({ hasText: "a reset link is on its way" })).toBeVisible();

  const link = await linkFromEmail(email, "reset-password");
  await page.goto(link);
  await expect(page.getByRole("heading", { name: "Choose a new password" })).toBeVisible();
  await expect(page).toHaveURL(/\/reset-password$/);
  await page.getByLabel("New password").fill(NEW_PASSWORD);
  await page.getByRole("button", { name: "Change password" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Your password has been changed" })).toBeVisible();

  // The link works once. (A new tab: in this one the link only changes the fragment.)
  const again = await page.context().newPage();
  await again.goto(link);
  await again.getByLabel("New password").fill(`${NEW_PASSWORD} again`);
  await again.getByRole("button", { name: "Change password" }).click();
  await expect(again.getByText("This link is invalid or has expired.")).toBeVisible();
  await again.close();

  await page.getByRole("link", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByText("Invalid email or password.")).toBeVisible();

  await page.getByLabel("Password").fill(NEW_PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  // Signed in: a user with no decks lands on the guided setup first.
  await skipGuidedSetup(page);
});
