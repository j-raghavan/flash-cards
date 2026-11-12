// SPDX-License-Identifier: AGPL-3.0-only
// AI usage, quotas and the spend cap for administrators (F-ADMIN-3, F-ADMIN-4,
// ADR 0016). Shapes mirror the admin routes of the API. Costs travel as
// integer micro-USD (millionths of a dollar), so sums stay exact; they are
// turned into dollars only for display and parsed back from dollars on input.

import { request } from "./client";
import type { CreatedInvite } from "./types";

/** A billing month, e.g. "2026-11", as the server accepts it. */
export const PERIOD_PATTERN = /^\d{4}-(0[1-9]|1[0-2])$/;

export interface UserUsage {
  user_id: string;
  calls: number;
  input_tokens: number;
  output_tokens: number;
  cost_micro_usd: number;
  /** AI generation pages used against the monthly quota this month. */
  pages: number;
}

export interface ProviderUsage {
  provider: string;
  model: string;
  calls: number;
  input_tokens: number;
  output_tokens: number;
  cost_micro_usd: number;
}

export interface UsageReport {
  period: string;
  cost_micro_usd: number;
  spend_cap_micro_usd: number | null;
  by_user: UserUsage[];
  by_provider: ProviderUsage[];
}

/** The server's bounds (domain/quotas.py and api/ai_schemas.py). */
export const MAX_MONTHLY_PAGES = 100_000;
export const MAX_SPEND_CAP_MICRO_USD = 10_000_000 * 1_000_000;

export interface InstanceQuotas {
  /** null: members have no monthly page limit (ADR 0027). */
  default_monthly_pages: number | null;
  /** null: no cap. */
  monthly_spend_cap_micro_usd: number | null;
}

export interface UserQuota {
  user_id: string;
  period: string;
  /** The limit in force: the override, or the instance default without one; null means unlimited. */
  monthly_pages: number | null;
  override: number | null;
  used_pages: number;
}

const id = encodeURIComponent;

export const adminUsage = {
  quotas: () => request<InstanceQuotas>("/admin/quotas"),
  setQuotas: (quotas: InstanceQuotas) => request<InstanceQuotas>("/admin/quotas", { method: "PUT", json: quotas }),
  userQuota: (userId: string) => request<UserQuota>(`/admin/users/${id(userId)}/quota`),
  /** null clears the override, so the instance default applies. */
  setUserQuota: (userId: string, monthlyPages: number | null) =>
    request<UserQuota>(`/admin/users/${id(userId)}/quota`, { method: "PUT", json: { monthly_pages: monthlyPages } }),
  /**
   * Email an invite again with a new token (F-ADMIN-1): the previous link stops
   * working and the expiry is unchanged. A link-only invite answers 409.
   */
  resendInvite: (inviteId: string) =>
    request<CreatedInvite>(`/admin/invites/${id(inviteId)}/resend`, { method: "POST" }),
  /** The month's usage; null asks for the current month. */
  usage: (period: string | null) =>
    request<UsageReport>(
      period === null ? "/admin/usage" : `/admin/usage?${new URLSearchParams({ period }).toString()}`,
    ),
};

// --- money ---

export const MICRO_PER_USD = 1_000_000;

/** Micro-USD as dollars, e.g. "$12.50"; amounts under a cent keep their digits ("$0.0042"). */
export function formatUsd(micro: number): string {
  const small = micro !== 0 && Math.abs(micro) < MICRO_PER_USD / 100;
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: small ? 6 : 2,
  }).format(micro / MICRO_PER_USD);
}

/**
 * Dollars typed by a person ("25", "12.50", "$0.005") as micro-USD, or null when
 * the text is not an amount. Parsed digit by digit, so there is no float rounding.
 */
export function parseUsd(text: string): number | null {
  const match = /^\$?(\d{1,8})(?:\.(\d{0,6}))?$/.exec(text.trim());
  if (match === null) {
    return null;
  }
  const whole = Number(match[1]);
  const fraction = Number((match[2] ?? "").padEnd(6, "0"));
  return whole * MICRO_PER_USD + fraction;
}

/** Micro-USD as plain dollars for an input, e.g. 12_500_000 as "12.5". */
export function usdInput(micro: number): string {
  const whole = Math.floor(micro / MICRO_PER_USD);
  const fraction = String(micro % MICRO_PER_USD)
    .padStart(6, "0")
    .replace(/0+$/, "");
  return fraction === "" ? String(whole) : `${String(whole)}.${fraction}`;
}

/** A whole page count within the server's bounds (0 to MAX_MONTHLY_PAGES), or null. */
export function parsePages(text: string): number | null {
  const trimmed = text.trim();
  if (!/^\d{1,6}$/.test(trimmed)) {
    return null;
  }
  const pages = Number(trimmed);
  return pages <= MAX_MONTHLY_PAGES ? pages : null;
}

// --- billing months ---

/** The current billing month in UTC, the server's clock. */
export function currentPeriod(now: Date = new Date()): string {
  return `${String(now.getUTCFullYear())}-${String(now.getUTCMonth() + 1).padStart(2, "0")}`;
}

/** The month `delta` months after `period`. */
export function shiftPeriod(period: string, delta: number): string {
  const [year, month] = period.split("-").map(Number) as [number, number];
  return currentPeriod(new Date(Date.UTC(year, month - 1 + delta, 1)));
}

/** "2026-11" as "November 2026". */
export function periodLabel(period: string): string {
  const [year, month] = period.split("-").map(Number) as [number, number];
  return new Date(Date.UTC(year, month - 1, 1)).toLocaleDateString(undefined, {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  });
}
