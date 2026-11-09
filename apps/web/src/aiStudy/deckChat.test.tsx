// SPDX-License-Identifier: AGPL-3.0-only
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { ChatAnswer } from "../api/aiStudy";
import type { Deck } from "../api/types";
import { USER, mockApi, renderApp, type Call } from "../test/helpers";

const DECKS: Deck[] = [{ id: "d1", name: "Biology", parent_id: null, created_at: "2026-01-01T00:00:00Z" }];
const page = { "GET /auth/me": { body: USER }, "GET /decks": { body: DECKS } };

async function panel(): Promise<HTMLElement> {
  return screen.findByRole("region", { name: "Ask your source documents" });
}

function asked(calls: Call[]): unknown[] {
  return calls.filter((c) => c.path === "/decks/d1/chat").map((c) => c.body);
}

describe("document chat", () => {
  it("asks with Enter, keeps Shift+Enter for new lines, and cites file and page", async () => {
    const answers: ChatAnswer[] = [
      {
        answer: "ATP is made in the **mitochondria**. <img src=x onerror=alert(1)> [Sign in](https://evil.example/login)",
        citations: [
          { file_id: "f-a", page: 3 },
          { file_id: "f-b", page: 1 },
          { file_id: "f-a", page: 3 },
        ],
      },
      { answer: "Osmosis moves water.", citations: [{ file_id: "f-b", page: 7 }, { file_id: "f-c", page: 2 }] },
      { answer: "I could not find that.", citations: [] },
    ];
    let reply = 0;
    const calls = mockApi({ ...page, "POST /decks/d1/chat": () => ({ body: answers[reply++] }) });
    renderApp("/decks/d1");
    const region = await panel();
    const user = userEvent.setup();
    const box = within(region).getByLabelText("Your question");
    expect(within(region).getByRole("button", { name: "Ask" })).toBeDisabled();

    await user.type(box, "Where is ATP made?{Shift>}{Enter}{/Shift}Briefly.");
    expect(box).toHaveValue("Where is ATP made?\nBriefly.");
    await user.keyboard("{Enter}");
    expect(box).toHaveValue("");
    expect(box).toHaveFocus();

    const log = within(region).getByRole("log", { name: "Conversation" });
    expect(await within(log).findByText("mitochondria")).toBeInTheDocument();
    expect(log.querySelector("img[onerror]")).toBeNull();
    // A link the model repeats from a document is text, never something to click.
    expect(log.querySelector("a")).toBeNull();
    expect(log).toHaveTextContent("Sign in (https://evil.example/login)");
    expect(within(log).getByText(/Where is ATP made\?/)).toBeInTheDocument();
    expect(within(log).getByText(/AI-generated answer/)).toBeInTheDocument();
    const first = within(log).getAllByRole("list", { name: "Sources" })[0] as HTMLElement;
    expect(within(first).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "Document 1, page 3",
      "Document 2, page 1",
    ]);

    await user.type(box, "And osmosis?");
    await user.click(within(region).getByRole("button", { name: "Ask" }));
    expect(await within(log).findByText("Osmosis moves water.")).toBeInTheDocument();
    const second = within(log).getAllByRole("list", { name: "Sources" })[1] as HTMLElement;
    expect(within(second).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "Document 2, page 7",
      "Document 3, page 2",
    ]);

    await user.type(box, "Anything else?{Enter}");
    expect(await within(log).findByText("No pages cited.")).toBeInTheDocument();
    expect(asked(calls)).toEqual([
      { question: "Where is ATP made?\nBriefly." },
      { question: "And osmosis?" },
      { question: "Anything else?" },
    ]);
  });

  it("teaches the next step when the deck has no source documents", async () => {
    let reply = 0;
    const calls = mockApi({
      ...page,
      "POST /decks/d1/chat": () =>
        reply++ === 0
          ? { status: 409, body: { detail: "This deck has no source documents with text to chat with." } }
          : { body: { answer: "Now it works.", citations: [] } },
    });
    renderApp("/decks/d1");
    const region = await panel();
    const user = userEvent.setup();
    const box = within(region).getByLabelText("Your question");
    await user.type(box, "What is ATP?{Enter}");
    expect(await within(region).findByText("This deck has no source documents to chat with yet.")).toBeInTheDocument();
    expect(within(region).getByRole("link", { name: "Generate cards from a file" })).toHaveAttribute(
      "href",
      "/decks/d1/generate",
    );
    expect(box).toHaveValue("What is ATP?");
    expect(within(region).queryByText("Looking through your documents…")).not.toBeInTheDocument();

    await user.keyboard("{Enter}");
    expect(await within(region).findByText("Now it works.")).toBeInTheDocument();
    expect(within(region).queryByText(/no source documents/)).not.toBeInTheDocument();
    expect(asked(calls)).toHaveLength(2);
  });

  it("shows a pending answer, then AI failures inside the conversation", async () => {
    let release: (() => void) | null = null;
    let reply = 0;
    mockApi({
      ...page,
      "POST /decks/d1/chat": () => {
        reply += 1;
        if (reply === 1) {
          return new Promise((resolve) => {
            release = () => {
              resolve({ status: 429, body: { detail: "Daily AI request limit reached." } });
            };
          });
        }
        return reply === 2 ? { status: 502, body: { detail: "x" } } : new TypeError("Failed to fetch");
      },
    });
    renderApp("/decks/d1");
    const region = await panel();
    const user = userEvent.setup();
    const box = within(region).getByLabelText("Your question");
    await user.type(box, "One?{Enter}");
    expect(await within(region).findByText("Looking through your documents…")).toBeInTheDocument();
    expect(within(region).getByRole("button", { name: "Asking…" })).toBeDisabled();
    expect(within(region).getByLabelText("Your question")).toHaveFocus();
    await user.type(box, "Two?{Enter}"); // ignored while an answer is on its way
    expect(box).toHaveValue("Two?");
    (release as unknown as () => void)();
    expect(await within(region).findByText(/Daily AI request limit reached\. Everything else/)).toBeInTheDocument();
    await user.keyboard("{Enter}");
    await waitFor(() => {
      expect(within(region).getByText(/could not be used/)).toBeInTheDocument();
    });
    await user.type(box, "Three?{Enter}");
    expect(await within(region).findByText(/Could not reach the server/)).toBeInTheDocument();
  });

  it("drops an answer that arrives after leaving the page", async () => {
    let release: (() => void) | null = null;
    mockApi({
      ...page,
      "POST /decks/d1/chat": () =>
        new Promise((resolve) => {
          release = () => {
            resolve({ body: { answer: "Late.", citations: [] } });
          };
        }),
      "GET /decks/d1/review": { body: [] },
    });
    renderApp("/decks/d1");
    const region = await panel();
    const user = userEvent.setup();
    await user.type(within(region).getByLabelText("Your question"), "Q{Enter}");
    await user.click(screen.getByRole("link", { name: "Study now" }));
    expect(await screen.findByText("Nothing is due in this deck right now.")).toBeInTheDocument();
    (release as unknown as () => void)();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(screen.queryByText("Late.")).not.toBeInTheDocument();
  });

  it("ignores a request that was aborted", async () => {
    mockApi({
      ...page,
      "POST /decks/d1/chat": () => {
        throw new DOMException("Aborted", "AbortError");
      },
    });
    renderApp("/decks/d1");
    const region = await panel();
    const user = userEvent.setup();
    await user.type(within(region).getByLabelText("Your question"), "Q{Enter}");
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(within(region).getByText("Looking through your documents…")).toBeInTheDocument();
  });
});
