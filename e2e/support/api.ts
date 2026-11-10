// SPDX-License-Identifier: AGPL-3.0-only
// Arranging test data through the real API, so each test sets up only what it
// needs and drives the browser for the journey it is about.
import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";

import { expect, request, type APIRequestContext, type APIResponse } from "@playwright/test";

import { ADMIN_STATE, API_LOG, WEB_URL } from "./env";

/** A password that meets the server's rules (at least 12 characters). */
export const PASSWORD = "e2e correct horse battery";

/** A fresh address per user, so tests never share accounts or data. */
export function uniqueEmail(label: string): string {
  const slug = label
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 24);
  return `e2e-${slug || "user"}-${randomUUID().slice(0, 8)}@example.com`;
}

// Not a browser, so the API's CSRF check (ADR 0017) asks for this header.
const HEADERS = { "X-Requested-With": "e2e" };

async function json<T>(response: APIResponse, what: string): Promise<T> {
  if (!response.ok()) {
    throw new Error(`${what} failed: ${String(response.status())} ${await response.text()}`);
  }
  return (await response.json()) as T;
}

/**
 * A thin client for the API under /api on the web origin (through the preview
 * server's proxy). Built on a browser context's request object it shares that
 * context's session cookie, so a user signed in here is signed in in the pages.
 */
export class Api {
  constructor(private readonly ctx: APIRequestContext) {}

  async get<T>(path: string): Promise<T> {
    return json<T>(await this.ctx.get(`/api${path}`, { headers: HEADERS }), `GET ${path}`);
  }

  async post<T>(path: string, data?: unknown): Promise<T> {
    return json<T>(await this.ctx.post(`/api${path}`, { headers: HEADERS, data }), `POST ${path}`);
  }

  async createDeck(name: string): Promise<Deck> {
    return this.post<Deck>("/decks", { name, parent_id: null });
  }

  async addBasicCard(deckId: string, front: string, back: string, tags: string[] = []): Promise<void> {
    await this.post(`/decks/${deckId}/notes`, { note_type: "basic", fields: { front, back }, tags });
  }

  async deleteDeck(deckId: string): Promise<void> {
    const response = await this.ctx.delete(`/api/decks/${deckId}`, { headers: HEADERS });
    if (!response.ok()) {
      throw new Error(`DELETE /decks/${deckId} failed: ${String(response.status())} ${await response.text()}`);
    }
  }

  async deckStats(deckId: string): Promise<DeckStats> {
    return this.get<DeckStats>(`/decks/${deckId}/stats`);
  }

  async exportAccount(): Promise<AccountExport> {
    return this.get<AccountExport>("/account/export");
  }
}

export interface Deck {
  id: string;
  name: string;
}

export interface DeckStats {
  total: number;
  new: number;
  learning: number;
  review: number;
  due: number;
}

export interface AccountExport {
  user: { email: string };
  decks: { id: string; name: string }[];
  cards: { id: string; deck_id: string }[];
  review_logs: { id: string; card_id: string; rating: number }[];
}

/** The admin signed up in global setup, with a session of its own. */
export async function adminApi(): Promise<{ api: Api; dispose: () => Promise<void> }> {
  const ctx = await request.newContext({ baseURL: WEB_URL, storageState: ADMIN_STATE });
  return { api: new Api(ctx), dispose: () => ctx.dispose() };
}

/** An admin creates an invite through the API and returns its one-time token. */
export async function createInvite(role: "member" | "admin" = "member"): Promise<string> {
  const admin = await adminApi();
  try {
    const created = await admin.api.post<{ token: string }>("/admin/invites", { role });
    return created.token;
  } finally {
    await admin.dispose();
  }
}

/** Sign up through the API with a fresh invite; the context is then signed in. */
export async function signUpWithApi(
  ctx: APIRequestContext,
  email: string,
  role: "member" | "admin" = "member",
): Promise<Api> {
  const token = await createInvite(role);
  const api = new Api(ctx);
  await api.post("/auth/signup", { token, email, password: PASSWORD });
  return api;
}

/** The API's log, where the development mailer writes every email it would send. */
export function apiLog(): string {
  try {
    return readFileSync(API_LOG, "utf8");
  } catch {
    return "";
  }
}

/** Wait for the email to `to` whose body has a link to `page`, and return that link. */
export async function linkFromEmail(to: string, page: string): Promise<string> {
  // The link must be in this email's own body: the match may not run past the
  // start of the next log record into someone else's email.
  const sameRecord = "(?:(?!\\n(?:DEBUG|INFO|WARNING|ERROR|CRITICAL) )[\\s\\S])*?";
  const pattern = new RegExp(
    `DEV MAIL \\(not sent\\) to ${escapeRegExp(to)}:${sameRecord}(${escapeRegExp(WEB_URL)}/${page}#token=[^\\s]+)`,
  );
  let link = "";
  await expect
    .poll(
      () => {
        link = pattern.exec(apiLog())?.[1] ?? "";
        return link;
      },
      { message: `an email to ${to} with a /${page} link`, timeout: 15_000 },
    )
    .not.toBe("");
  return link;
}

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
