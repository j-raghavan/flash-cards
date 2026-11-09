// SPDX-License-Identifier: AGPL-3.0-only
// Runs once, after both servers are up: signs up the instance's first admin with
// the bootstrap invite the API logged at startup, and saves its session for the
// tests, which use it to create their own invites.
import { expect, request } from "@playwright/test";

import { apiLog, PASSWORD } from "./api";
import { ADMIN_STATE, WEB_URL } from "./env";

const BOOTSTRAP = /DEV BOOTSTRAP \(not for production\): admin invite token = (\S+)/;

export default async function globalSetup(): Promise<void> {
  let token = "";
  await expect
    .poll(
      () => {
        token = BOOTSTRAP.exec(apiLog())?.[1] ?? "";
        return token;
      },
      { message: "the bootstrap invite in the API log", timeout: 15_000 },
    )
    .not.toBe("");

  const ctx = await request.newContext({ baseURL: WEB_URL });
  try {
    const response = await ctx.post("/api/auth/signup", {
      headers: { "X-Requested-With": "e2e" },
      data: { token, email: "e2e-admin@example.com", password: PASSWORD },
    });
    if (!response.ok()) {
      throw new Error(`admin sign-up failed: ${String(response.status())} ${await response.text()}`);
    }
    await ctx.storageState({ path: ADMIN_STATE });
  } finally {
    await ctx.dispose();
  }
}
