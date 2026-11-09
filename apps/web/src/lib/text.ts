// SPDX-License-Identifier: AGPL-3.0-only

/** Tags are typed space- or comma-separated; tags themselves contain no spaces. */
export function parseTags(input: string): string[] {
  const seen = new Set<string>();
  const tags: string[] = [];
  for (const raw of input.split(/[\s,]+/)) {
    const tag = raw.trim();
    if (tag !== "" && !seen.has(tag.toLowerCase())) {
      seen.add(tag.toLowerCase());
      tags.push(tag);
    }
  }
  return tags;
}

/**
 * The token from a link's fragment (`#token=...`). The server puts tokens in the
 * fragment so they never reach access logs; a `?token=` query is accepted too.
 */
export function tokenFromLocation(hash: string, search: string): string {
  const fromHash = new URLSearchParams(hash.replace(/^#/, "")).get("token");
  if (fromHash !== null && fromHash !== "") {
    return fromHash;
  }
  return new URLSearchParams(search).get("token") ?? "";
}

/** Only same-app paths are allowed as a post-login destination. */
export function safeNextPath(next: string | null): string {
  if (next === null || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) {
    return "/";
  }
  return next;
}

export function newIdempotencyKey(): string {
  return crypto.randomUUID();
}
