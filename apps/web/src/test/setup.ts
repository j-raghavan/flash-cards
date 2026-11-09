// SPDX-License-Identifier: AGPL-3.0-only
import "@testing-library/jest-dom/vitest";
import "fake-indexeddb/auto";

import { cleanup } from "@testing-library/react";
import { IDBFactory } from "fake-indexeddb";
import { afterEach, beforeEach } from "vitest";

import { onUnauthorized } from "../api/client";
import { resetForTests } from "../offline/reviewQueue";
import { resetOnboardingForTests } from "../onboarding/state";

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
