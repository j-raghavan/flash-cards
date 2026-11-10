// SPDX-License-Identifier: AGPL-3.0-only
import "@testing-library/jest-dom/vitest";
import "fake-indexeddb/auto";

import { cleanup, configure } from "@testing-library/react";
import { IDBFactory } from "fake-indexeddb";
import { afterEach, beforeEach } from "vitest";

import { onUnauthorized } from "../api/client";
import { resetForTests } from "../offline/reviewQueue";
import { resetOnboardingForTests } from "../onboarding/state";

// Several pages load on first visit (React.lazy), and importing a chunk on a busy
// CI runner can take longer than Testing Library's 1 s default for findBy* and
// waitFor. A longer ceiling only slows a test that is already failing.
configure({ asyncUtilTimeout: 5_000 });

beforeEach(async () => {
  await resetForTests();
  globalThis.indexedDB = new IDBFactory();
  localStorage.clear();
  resetOnboardingForTests();
  Object.defineProperty(navigator, "onLine", { configurable: true, get: () => true });
});

afterEach(() => {
  cleanup();
  onUnauthorized(null);
});
