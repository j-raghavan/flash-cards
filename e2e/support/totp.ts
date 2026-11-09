// SPDX-License-Identifier: AGPL-3.0-only
// RFC 6238 codes, as an authenticator app computes them (6 digits, 30-second
// steps, HMAC-SHA1), so a test can answer the second sign-in step.
import { createHmac } from "node:crypto";

const ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";

function base32(secret: string): Buffer {
  let bits = "";
  for (const char of secret.replace(/=+$/, "").toUpperCase()) {
    const value = ALPHABET.indexOf(char);
    if (value < 0) {
      throw new Error(`not base32: ${char}`);
    }
    bits += value.toString(2).padStart(5, "0");
  }
  const bytes: number[] = [];
  for (let i = 0; i + 8 <= bits.length; i += 8) {
    bytes.push(parseInt(bits.slice(i, i + 8), 2));
  }
  return Buffer.from(bytes);
}

/** The code for the time step `offset` steps from now. */
export function totpCode(secret: string, offset = 0, now: number = Date.now()): string {
  const step = Math.floor(now / 1000 / 30) + offset;
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(step));
  const digest = createHmac("sha1", base32(secret)).update(counter).digest();
  const start = (digest[digest.length - 1] ?? 0) & 0x0f;
  const value = digest.readUInt32BE(start) & 0x7fffffff;
  return String(value % 1_000_000).padStart(6, "0");
}
