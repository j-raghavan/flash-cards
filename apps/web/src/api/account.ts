// SPDX-License-Identifier: AGPL-3.0-only
// Account routes: sessions (F-AUTH-2), public sign-up (F-AUTH-7), AI quota,
// data export and deletion (F-IMP-5), the AI data-use disclosure (F-LEGAL-2)
// and the operator's legal pages (F-LEGAL-1).
// Shapes mirror apps/api/src/flashcards/api/schemas.py and ai_schemas.py.

import type { Appearance } from "../lib/appearance";
import { request } from "./client";

/** A device-list entry; it never carries the session token. */
export interface DeviceSession {
  id: string;
  created_at: string;
  expires_at: string;
  last_seen_at: string | null;
  device: string;
  current: boolean;
}

export interface SessionsRevoked {
  revoked: number;
}

export interface AuthOptions {
  invite_only: boolean;
}

export interface AiQuota {
  /** The month counted against, as "YYYY-MM". */
  period: string;
  monthly_pages: number;
  used_pages: number;
  /** Most pages one uploaded document may have; null means no limit (an admin). */
  max_document_pages: number | null;
  /** False for an admin: their pages are counted but never refused. */
  quota_applies: boolean;
}

export interface AiProvider {
  name: string;
  terms_url: string | null;
}

export interface AiDisclosure {
  ai_enabled: boolean;
  trains_on_user_content: boolean;
  statements: string[];
  provider: AiProvider | null;
}

export type LegalPageName = "privacy" | "terms" | "imprint";

/** An operator-written legal page (F-LEGAL-1); the Markdown is rendered through the sanitizer. */
export interface LegalPageContent {
  page: LegalPageName;
  title: string;
  markdown: string;
  /** True while the operator has not filled the page in yet. */
  is_template: boolean;
}

const id = encodeURIComponent;

export const sessions = {
  list: () => request<DeviceSession[]>("/auth/sessions"),
  revoke: (sessionId: string) => request<undefined>(`/auth/sessions/${id(sessionId)}`, { method: "DELETE" }),
  revokeOthers: () => request<SessionsRevoked>("/auth/sessions/revoke-others", { method: "POST" }),
};

export const signup = {
  options: () => request<AuthOptions>("/auth/options"),
  /** Always 202 when accepted, whether or not the address is new; it never signs in. */
  register: (email: string, password: string) =>
    request<unknown>("/auth/register", { method: "POST", json: { email, password } }),
};

export const account = {
  aiQuota: () => request<AiQuota>("/account/ai-quota"),
  /** Everything the account holds, as one JSON document. */
  export: () => request<unknown>("/account/export"),
  remove: () => request<undefined>("/account", { method: "DELETE" }),
  /** Change the parts of the look given; it follows the user to every device. */
  setAppearance: (change: Partial<Appearance>) =>
    request<Appearance>("/account/appearance", { method: "PATCH", json: change }),
};

export const legal = {
  aiDisclosure: () => request<AiDisclosure>("/legal/ai-disclosure"),
  page: (name: LegalPageName) => request<LegalPageContent>(`/legal/${id(name)}`),
};

/** A provider terms link, only when it is a plain web address. */
export function safeTermsUrl(url: string | null): string | null {
  if (url === null) {
    return null;
  }
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.href : null;
  } catch {
    return null;
  }
}

// --- the AI notice on the generate page, remembered per user on this device ---

const NOTICE_KEY = "flashcards.aiNoticeSeen.";

export function aiNoticeSeen(userId: string): boolean {
  try {
    return localStorage.getItem(NOTICE_KEY + userId) === "1";
  } catch {
    return false;
  }
}

export function rememberAiNoticeSeen(userId: string): void {
  try {
    localStorage.setItem(NOTICE_KEY + userId, "1");
  } catch {
    // Storage unavailable (private mode): the notice shows again next time.
  }
}

export function forgetAiNotice(userId: string): void {
  try {
    localStorage.removeItem(NOTICE_KEY + userId);
  } catch {
    // Nothing stored.
  }
}
