// SPDX-License-Identifier: AGPL-3.0-only
import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { LegalPageContent } from "../api/account";
import { renderMarkdown } from "../lib/markdown";
import { USER, mockApi, renderApp } from "../test/helpers";

const signedOut = { "GET /auth/me": { status: 401, body: { detail: "Not authenticated." } } };

const TEMPLATE: LegalPageContent = {
  page: "privacy",
  title: "Privacy policy",
  markdown: "> **The operator of this server has not filled in this page yet.**\n\n## Who is responsible",
  is_template: true,
};

describe("legal pages", () => {
  it("shows the operator's template to a signed-out visitor and says it is unfilled", async () => {
    const calls = mockApi({ ...signedOut, "GET /legal/privacy": { body: TEMPLATE } });
    renderApp("/privacy");
    expect(await screen.findByRole("heading", { level: 1, name: "Privacy policy" })).toBeInTheDocument();
    expect(await screen.findByRole("note")).toHaveTextContent("has not written this page yet");
    expect(screen.getByRole("heading", { level: 2, name: "Who is responsible" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login");
    const footer = screen.getByRole("navigation", { name: "Legal" });
    expect(within(footer).getByRole("link", { name: "Terms" })).toHaveAttribute("href", "/terms");
    expect(within(footer).getByRole("link", { name: "Imprint" })).toHaveAttribute("href", "/imprint");
    expect(document.title).toContain("Privacy policy");
    expect(calls.some((c) => c.path === "/legal/privacy")).toBe(true);
  });

  it("renders an operator's page through the sanitizer, without math", async () => {
    mockApi({
      "GET /auth/me": { body: USER },
      "GET /legal/terms": {
        body: {
          page: "terms",
          title: "Terms of use",
          markdown:
            'Plans cost $5 or $10 a month.\n\n<script>window.pwned = true</script><img src="https://tracker.example/p.gif" onerror="alert(1)">\n\n[Contact](mailto:ops@example.org)',
          is_template: false,
        } satisfies LegalPageContent,
      },
    });
    const { container } = renderApp("/terms");
    expect(await screen.findByText("Plans cost $5 or $10 a month.")).toBeInTheDocument();
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("[onerror]")).toBeNull();
    expect(container.querySelector('img[src^="https://tracker"]')).toBeNull();
    expect(container.querySelector(".katex")).toBeNull();
    expect(screen.getByRole("link", { name: "Contact" })).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getByRole("link", { name: "Back to your decks" })).toHaveAttribute("href", "/");
  });

  it("says when a page cannot load", async () => {
    mockApi({ ...signedOut, "GET /legal/imprint": { status: 500 } });
    renderApp("/imprint");
    expect(await screen.findByRole("alert")).toHaveTextContent(/server/);
  });

  it("is linked from the sign-in, sign-up and signed-in screens", async () => {
    mockApi({ ...signedOut, "GET /auth/options": { body: { invite_only: false } } });
    const { unmount } = renderApp("/signup");
    expect(await screen.findByRole("link", { name: "terms of use" })).toHaveAttribute("href", "/terms");
    expect(screen.getByRole("link", { name: "privacy policy" })).toHaveAttribute("href", "/privacy");
    expect(screen.getByRole("navigation", { name: "Legal" })).toBeInTheDocument();
    unmount();

    mockApi(signedOut);
    const invite = renderApp("/signup#token=inv-1");
    expect(await screen.findByRole("link", { name: "terms of use" })).toBeInTheDocument();
    invite.unmount();

    mockApi({
      "GET /auth/me": { body: USER },
      "GET /decks": { body: [{ id: "d1", name: "Bio", parent_id: null, created_at: "2026-01-01T00:00:00Z" }] },
    });
    renderApp("/");
    expect(await screen.findByRole("link", { name: "Bio" })).toBeInTheDocument();
    expect(
      within(screen.getByRole("navigation", { name: "Legal" })).getByRole("link", { name: "Privacy" }),
    ).toHaveAttribute("href", "/privacy");
  });
});

describe("renderMarkdown without math", () => {
  it("leaves dollar signs and TeX delimiters as text", () => {
    expect(renderMarkdown("$x$ and \\(y\\)", { math: false })).not.toContain("katex");
    expect(renderMarkdown("$x$")).toContain("katex");
    expect(renderMarkdown("MATHPLACEHOLDER0END stays", { math: false })).toContain("MATHPLACEHOLDER0END stays");
  });
});
