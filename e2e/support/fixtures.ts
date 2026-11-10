// SPDX-License-Identifier: AGPL-3.0-only
// Test fixtures. Every test that uses `member` gets a brand-new account, signed in
// in its own browser context, so tests share no users or data and can run in
// parallel against the one in-memory API.
import { request, test as base } from "@playwright/test";

import { signUpWithApi, uniqueEmail, type Api } from "./api";
import { WEB_URL } from "./env";

export interface Member {
  email: string;
  api: Api;
}

/**
 * An admin of its own, signed in through the API only. Every member sees an
 * active admin's decks (ADR 0024), so each deck it creates is deleted when the
 * test ends, pass or fail.
 */
export interface LibraryAdmin {
  api: Api;
  createDeck: (name: string) => Promise<{ id: string; name: string }>;
}

export const test = base.extend<{ member: Member; admin: LibraryAdmin; libraryAdmin: LibraryAdmin }>({
  member: async ({ context }, use, testInfo) => {
    const email = uniqueEmail(testInfo.title);
    const api = await signUpWithApi(context.request, email);
    await use({ email, api });
  },
  // A new admin signed in in the page's own browser context, for admin-only
  // screens. Its decks reach every member's library, so a spec that uses it runs
  // after the rest of the suite (see playwright.config.ts), and each deck it
  // creates is deleted when the test ends, pass or fail.
  admin: async ({ context }, use, testInfo) => {
    const api = await signUpWithApi(context.request, uniqueEmail(`admin ${testInfo.title}`), "admin");
    const created: string[] = [];
    try {
      await use({
        api,
        createDeck: async (name) => {
          const deck = await api.createDeck(name);
          created.push(deck.id);
          return deck;
        },
      });
    } finally {
      for (const deckId of created) {
        await api.deleteDeck(deckId);
      }
    }
  },
  libraryAdmin: async ({}, use, testInfo) => {
    const ctx = await request.newContext({ baseURL: WEB_URL });
    const created: string[] = [];
    try {
      const api = await signUpWithApi(ctx, uniqueEmail(`admin ${testInfo.title}`), "admin");
      await use({
        api,
        createDeck: async (name) => {
          const deck = await api.createDeck(name);
          created.push(deck.id);
          return deck;
        },
      });
      for (const deckId of created) {
        await api.deleteDeck(deckId);
      }
    } finally {
      await ctx.dispose();
    }
  },
});

export { expect } from "@playwright/test";
