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

/**
 * A random version 4 UUID. crypto.randomUUID exists only in secure contexts
 * (HTTPS or localhost); on a plain-HTTP home server the key is built from
 * crypto.getRandomValues, which every context has.
 */
export function newIdempotencyKey(): string {
  if (typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  const hex = Array.from(crypto.getRandomValues(new Uint8Array(16)), (byte) => byte.toString(16).padStart(2, "0")).join(
    "",
  );
  // Version 4 in the 13th digit; the RFC 4122 variant (8, 9, a or b) in the 17th.
  const variant = ((Number.parseInt(hex.charAt(16), 16) & 0x3) | 0x8).toString(16);
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-4${hex.slice(13, 16)}-${variant}${hex.slice(17, 20)}-${hex.slice(20)}`;
}
