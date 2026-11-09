// SPDX-License-Identifier: AGPL-3.0-only
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { Deck } from "../api/types";
import { USER, mockApi, renderApp } from "../test/helpers";

const DECKS: Deck[] = [{ id: "d1", name: "Biology", parent_id: null, created_at: "2026-01-01T00:00:00Z" }];
const page = { "GET /auth/me": { body: USER }, "GET /decks": { body: DECKS } };

async function panel(): Promise<HTMLElement> {
  return screen.findByRole("region", { name: "Deck summary" });
}

describe("deck summary", () => {
  it("is asked for, never fetched on its own, and rendered sanitized with an AI label", async () => {
    const calls = mockApi({
      ...page,
      "POST /decks/d1/summary": {
        body: {
          summary: "Cells make **energy**. <img src=x onerror=alert(1)><a href=\"javascript:alert(1)\">x</a>",
          key_points: ["ATP stores energy", "<script>alert(1)</script>Mitochondria"],
        },
      },
    });
    renderApp("/decks/d1");
    const region = await panel();
    expect(calls.some((c) => c.path === "/decks/d1/summary")).toBe(false);
    const user = userEvent.setup();
    await user.click(within(region).getByRole("button", { name: "Summarize this deck" }));

    expect(await within(region).findByText("energy")).toBeInTheDocument();
    expect(within(region).getByText("Summary ready.")).toBeInTheDocument();
    expect(within(region).getByText(/AI-generated/)).toBeInTheDocument();
    expect(within(region).getByRole("heading", { name: "Key points" })).toBeInTheDocument();
    expect(within(region).getByText("ATP stores energy")).toBeInTheDocument();
    expect(region.querySelector("img[onerror], script, a[href^='javascript']")).toBeNull();
    expect(within(region).getByRole("button", { name: "Summarize again" })).toBeEnabled();
  });

  it("leaves out an empty key point list", async () => {
    mockApi({ ...page, "POST /decks/d1/summary": { body: { summary: "Short.", key_points: [] } } });
    renderApp("/decks/d1");
    const region = await panel();
    await userEvent.setup().click(within(region).getByRole("button", { name: "Summarize this deck" }));
    expect(await within(region).findByText("Short.")).toBeInTheDocument();
    expect(within(region).queryByRole("heading", { name: "Key points" })).not.toBeInTheDocument();
  });

  it("explains the unlock threshold and AI failures", async () => {
    let reply = 0;
    const replies = [
      { status: 409, body: { detail: "summary needs at least 20 cards; the deck has 20" } },
      { status: 429, body: { detail: "This instance's monthly AI budget is used up." } },
    ];
    mockApi({ ...page, "POST /decks/d1/summary": () => replies[reply++] ?? { status: 500 } });
    renderApp("/decks/d1");
    const region = await panel();
    const user = userEvent.setup();
    const button = within(region).getByRole("button", { name: "Summarize this deck" });
    await user.click(button);
    expect(
      await within(region).findByText(/Deck summaries unlock once a deck has 20 cards. This deck has 20\./),
    ).toBeInTheDocument();
    await user.click(button);
    await waitFor(() => {
      expect(within(region).getByRole("alert")).toHaveTextContent("monthly AI budget is used up");
    });
    expect(within(region).queryByText(/unlock once/)).not.toBeInTheDocument();
  });

  it("drops a summary that arrives after leaving the page, and ignores clicks while busy", async () => {
    let release: (() => void) | null = null;
    const calls = mockApi({
      ...page,
      "POST /decks/d1/summary": () =>
        new Promise((resolve) => {
          release = () => {
            resolve({ body: { summary: "Late.", key_points: [] } });
          };
        }),
      "GET /decks/d1/review": { body: [] },
    });
    renderApp("/decks/d1");
    const region = await panel();
    const user = userEvent.setup();
    const button = within(region).getByRole("button", { name: "Summarize this deck" });
    await user.click(button);
    expect(button).toHaveAttribute("aria-disabled", "true");
    expect(button).toHaveFocus();
    await user.click(button);
    expect(calls.filter((c) => c.path === "/decks/d1/summary")).toHaveLength(1);
    await user.click(screen.getByRole("link", { name: "Study now" }));
    expect(await screen.findByText("Nothing is due in this deck right now.")).toBeInTheDocument();
    (release as unknown as () => void)();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(screen.queryByText("Late.")).not.toBeInTheDocument();
  });

  it("ignores a request that was aborted", async () => {
    mockApi({
      ...page,
      "POST /decks/d1/summary": () => {
        throw new DOMException("Aborted", "AbortError");
      },
    });
    renderApp("/decks/d1");
    const region = await panel();
    const user = userEvent.setup();
    await user.click(within(region).getByRole("button", { name: "Summarize this deck" }));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(within(region).queryByRole("alert")).not.toBeInTheDocument();
    expect(within(region).getByText("Writing the summary…")).toBeInTheDocument();
  });
});
