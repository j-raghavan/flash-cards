// SPDX-License-Identifier: AGPL-3.0-only
// Test fixtures. Every test that uses `member` gets a brand-new account, signed in
// in its own browser context, so tests share no users or data and can run in
// parallel against the one in-memory API.
import { test as base } from "@playwright/test";

import { signUpWithApi, uniqueEmail, type Api } from "./api";

export interface Member {
  email: string;
  api: Api;
}

export const test = base.extend<{ member: Member }>({
  member: async ({ context }, use, testInfo) => {
    const email = uniqueEmail(testInfo.title);
    const api = await signUpWithApi(context.request, email);
    await use({ email, api });
  },
});

export { expect } from "@playwright/test";
