// SPDX-License-Identifier: AGPL-3.0-only
// End-to-end suite: the real API (in-memory mode, fake AI provider) and the real
// production web build (vite preview, whose /api proxy forwards to the API).
import { existsSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { defineConfig, devices } from "@playwright/test";

import { API_PORT, API_URL, RUN_DIR, WEB_PORT, WEB_URL } from "./support/env";

const here = dirname(fileURLToPath(import.meta.url));
const repo = resolve(here, "..");
const isCI = process.env.CI !== undefined && process.env.CI !== "";

// The API's log (bootstrap invite, emails) is written here. Nothing is deleted:
// the API truncates its log when it starts and setup overwrites the rest, so
// loading this config again (a listing, an editor plugin) never pulls files from
// under a running suite.
mkdirSync(RUN_DIR, { recursive: true });

// The API's interpreter: E2E_PYTHON, else the local venv, else python on PATH (CI).
const localVenv = resolve(repo, "apps/api/.venv/bin/python");
const python = process.env.E2E_PYTHON ?? (existsSync(localVenv) ? localVenv : "python");

// Firefox and WebKit run only when asked for: E2E_BROWSERS=chromium,firefox,webkit.
const browsers = (process.env.E2E_BROWSERS ?? "chromium").split(",").map((name) => name.trim());
const allProjects = [
  { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  { name: "firefox", use: { ...devices["Desktop Firefox"] } },
  { name: "webkit", use: { ...devices["Desktop Safari"] } },
];

export default defineConfig({
  testDir: "./tests",
  globalSetup: "./support/global-setup.ts",
  fullyParallel: true,
  forbidOnly: isCI,
  retries: isCI ? 1 : 0,
  workers: isCI ? 2 : undefined,
  timeout: 30_000,
  expect: { timeout: 10_000 },
  // In CI the JSON results feed the job summary, which names flaky tests.
  reporter: isCI
    ? [["list"], ["html", { open: "never" }], ["github"], ["json", { outputFile: `${RUN_DIR}/results.json` }]]
    : [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: WEB_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    // Service workers are blocked: they would cache API responses across steps and
    // hide requests from the test. Offline review is driven by the app's own
    // IndexedDB queue, which needs no service worker once the page has loaded.
    serviceWorkers: "block",
  },
  projects: allProjects.filter((project) => browsers.includes(project.name)),
  webServer: [
    {
      command: `"${python}" -m uvicorn flashcards.api.app:app --host 127.0.0.1 --port ${String(API_PORT)} --log-config api-logging.json`,
      cwd: here,
      url: `${API_URL}/healthz`,
      reuseExistingServer: false,
      timeout: 60_000,
      stdout: "pipe",
      stderr: "pipe",
      env: {
        PYTHONUNBUFFERED: "1",
        // In-memory mode: no database URL, so every run starts empty.
        FLASHCARDS_DATABASE_URL: "",
        FLASHCARDS_ENVIRONMENT: "development",
        FLASHCARDS_DEV_BOOTSTRAP_INVITE: "true",
        FLASHCARDS_LLM_PROVIDER: "fake",
        FLASHCARDS_AI_PROVIDER_NAME: "the end-to-end test AI",
        FLASHCARDS_SESSION_COOKIE_SECURE: "false",
        FLASHCARDS_PUBLIC_BASE_URL: WEB_URL,
        FLASHCARDS_INVITE_ONLY: "true",
        FLASHCARDS_SMTP_HOST: "",
      },
    },
    {
      command: `npm run build && npm run preview -- --host 127.0.0.1 --port ${String(WEB_PORT)} --strictPort`,
      cwd: resolve(repo, "apps/web"),
      url: WEB_URL,
      reuseExistingServer: false,
      timeout: 180_000,
      stdout: "ignore",
      stderr: "pipe",
      env: { FLASHCARDS_API_PROXY_TARGET: API_URL },
    },
  ],
});
