// SPDX-License-Identifier: AGPL-3.0-only
// Who may change a deck, card or note (ADR 0024).

/**
 * Whether the caller owns it. A payload without `owned` (an API older than the
 * library, or an answer cached before an upgrade) counts as the caller's own, so
 * an owner never loses their controls; the server still refuses writes to
 * anything that is not theirs.
 */
export function isOwned(item: { owned?: boolean }): boolean {
  return item.owned !== false;
}
