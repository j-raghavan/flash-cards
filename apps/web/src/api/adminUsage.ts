// SPDX-License-Identifier: AGPL-3.0-only
// AI usage, quotas and the spend cap for administrators (F-ADMIN-3, F-ADMIN-4,
// ADR 0016). Shapes mirror the admin routes of the API. Costs travel as
// integer micro-USD (millionths of a dollar), so sums stay exact; they are
// turned into dollars only for display and parsed back from dollars on input.

import { request } from "./client";

/** A billing month, e.g. "2026-11", as the server accepts it. */
export const PERIOD_PATTERN = /^\d{4}-(0[1-9]|1[0-2])$/;

export interface UserUsage {
  user_id: string;
  calls: number;
  input_tokens: number;
  output_tokens: number;
  cost_micro_usd: number;
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

export const adminUsage = {
  /** The month's usage; null asks for the current month. */
  usage: (period: string | null) =>
    request<UsageReport>(period === null ? "/admin/usage" : `/admin/usage?${new URLSearchParams({ period }).toString()}`),
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
