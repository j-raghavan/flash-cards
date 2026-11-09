// SPDX-License-Identifier: AGPL-3.0-only
// Two-step sign-in with an authenticator app (F-AUTH-3): set it up on the
// account page, then sign in with the password and a code.
import { PASSWORD } from "../support/api";
import { expect, test } from "../support/fixtures";
import { skipGuidedSetup } from "../support/onboarding";
import { totpCode } from "../support/totp";

test("a member turns on an authenticator app and signs in with a code", async ({ page, member }) => {
  await page.goto("/account");
  const panel = page.getByRole("region", { name: "Two-step sign-in" });
  await expect(panel.getByText(/Two-step sign-in is off/)).toBeVisible();
  await panel.getByRole("button", { name: "Set up authenticator app" }).click();
  await panel.getByLabel("Your password").fill(PASSWORD);
  await panel.getByRole("button", { name: "Continue" }).click();
  await expect(panel.getByRole("img", { name: "QR code for your authenticator app" })).toBeVisible();
  const secret = await panel.getByLabel("Setup key").inputValue();
  await panel.getByLabel("The 6-digit code your app shows").fill(totpCode(secret));
  await panel.getByRole("button", { name: "Turn on" }).click();
  const codes = panel.getByRole("region", { name: "Your recovery codes" });
  await expect(codes.getByRole("listitem")).toHaveCount(10);
  await codes.getByRole("button", { name: "I have saved them" }).click();
  await expect(panel.getByText("Your authenticator app is on.")).toBeVisible();

  // Sign in again from scratch: the password alone no longer signs in.
  await page.context().clearCookies();
  await page.goto("/login");
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await page.getByLabel("Email").fill(member.email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Confirm it's you" })).toBeVisible();
  const field = page.getByLabel("Code from your authenticator app, or a recovery code");
  await field.fill("000000" === totpCode(secret, 1) ? "111111" : "000000");
  await page.getByRole("button", { name: "Verify" }).click();
  await expect(page.getByText("That code is not right.")).toBeVisible();
  // The code that turned the app on is spent: the next step's code signs in.
  await field.fill(totpCode(secret, 1));
  await page.getByRole("button", { name: "Verify" }).click();
  // A member with no decks lands on the guided first run.
  await skipGuidedSetup(page);
});
