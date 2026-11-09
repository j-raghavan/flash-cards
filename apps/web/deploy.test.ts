// SPDX-License-Identifier: AGPL-3.0-only
// The production Caddyfile repeats the app's Content-Security-Policy as a header
// (frame-ancestors is ignored in a <meta> tag); keep the two from drifting apart.

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { CONTENT_SECURITY_POLICY } from "./vite.config";

const caddyfile = readFileSync(join(import.meta.dirname, "Caddyfile"), "utf8");

describe("production Caddyfile", () => {
  it("sends the same Content-Security-Policy as the build", () => {
    const sent = /Content-Security-Policy "([^"]+)"/.exec(caddyfile)?.[1];
    expect(sent).toBe(CONTENT_SECURITY_POLICY);
  });

  it("long-caches only hashed assets and revalidates the shell and service worker", () => {
    // Only files that exist get the long lifetime; a missing asset's 404 does not.
    expect(caddyfile).toMatch(/handle \/assets\/\* \{[^}]*@asset file\s+header @asset Cache-Control "[^"]*immutable"/);
    expect(caddyfile).toMatch(/handle \{[^}]*Cache-Control "no-cache"[^}]*try_files \{path\} \/index\.html/);
  });

  it("strips /api before the API and streams its responses", () => {
    const start = caddyfile.indexOf("handle_path /api/* {");
    expect(start).toBeGreaterThan(0);
    const block = caddyfile.slice(start, caddyfile.indexOf("\n\t}", start));
    expect(block).toContain("reverse_proxy");
    expect(block).toContain("flush_interval -1");
  });
});
