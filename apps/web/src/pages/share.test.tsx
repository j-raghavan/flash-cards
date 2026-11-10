// SPDX-License-Identifier: AGPL-3.0-only
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { SharedDeck, ShareLink } from "../api/types";
import { USER, mockApi, renderApp } from "../test/helpers";

const me = { "GET /auth/me": { body: USER } };
const DECK = { id: "d1", name: "Biology", parent_id: null, created_at: "2026-01-01T00:00:00Z", owned: true };

const link = (id: string, over: Partial<ShareLink> = {}): ShareLink => ({
  id,
  deck_id: "d1",
  allow_clone: false,
  created_at: "2026-11-01T10:00:00Z",
  revoked_at: null,
  expires_at: null,
  ...over,
});

describe("share links", () => {
  it("creates a link shown once, toggles copying and revokes a link", async () => {
    const calls = mockApi({
      ...me,
      "GET /decks/d1": { body: DECK },
      "GET /decks/d1/shares": { body: [link("s1"), link("s0", { revoked_at: "2026-11-02T10:00:00Z" })] },
      "POST /decks/d1/shares": { status: 201, body: { ...link("s2", { allow_clone: true }), token: "tok en" } },
      "PATCH /shares/s1": (call) => ({ body: link("s1", call.body as Partial<ShareLink>) }),
      "POST /shares/s1/revoke": { body: link("s1", { revoked_at: "2026-11-08T10:00:00Z" }) },
    });
    renderApp("/decks/d1/share");
    const user = userEvent.setup();
    expect(await screen.findByRole("heading", { name: "Share “Biology”" })).toBeInTheDocument();
    await waitFor(() => {
      expect(document.title).toBe("Share Biology · Flash-Cards");
    });
    const rows = await screen.findAllByRole("row");
    expect(rows).toHaveLength(3);
    expect(within(rows[2] as HTMLElement).getByText(/^Revoked/)).toBeInTheDocument();
    expect(within(rows[2] as HTMLElement).getByRole("checkbox")).toBeDisabled();

    await user.click(screen.getByLabelText(/Allow people with the link to copy/));
    await user.click(screen.getByRole("button", { name: "Create link" }));
    const field = await screen.findByRole("textbox", { name: "Share link" });
    expect(field).toHaveValue(`${window.location.origin}/shared#token=tok%20en`);
    expect(screen.getByText(/preview and copy/)).toBeInTheDocument();
    field.focus();
    await user.click(screen.getByRole("button", { name: "Copy" }));
    expect(await screen.findByText("Copied.")).toBeInTheDocument();
    await expect(navigator.clipboard.readText()).resolves.toBe(`${window.location.origin}/shared#token=tok%20en`);
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ allow_clone: true, expires_in_days: 30 });
    expect(screen.getAllByRole("row")).toHaveLength(4);

    const toggle = screen
      .getAllByRole("checkbox", { name: /Allow copying the link created/, checked: false })
      .find((box) => !(box as HTMLInputElement).disabled) as HTMLElement;
    await user.click(toggle);
    await waitFor(() => {
      expect(screen.getAllByText("Allowed")).toHaveLength(2);
    });
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ allow_clone: true });

    const s1 = screen.getAllByRole("row")[2] as HTMLElement;
    await user.click(within(s1).getByRole("button", { name: "Revoke" }));
    await user.click(within(s1).getByRole("button", { name: "Revoke link" }));
    await waitFor(() => {
      expect(screen.getAllByText(/^Revoked/)).toHaveLength(2);
    });
  });

  it("says when there are no links and reports failures", async () => {
    mockApi({
      ...me,
      "GET /decks/d1": { status: 404, body: { detail: "Deck not found." } },
      "GET /decks/d1/shares": { body: [link("s1")] },
      "POST /decks/d1/shares": { status: 500 },
      "PATCH /shares/s1": { status: 500 },
      "POST /shares/s1/revoke": { status: 409, body: { detail: "Already revoked." } },
    });
    renderApp("/decks/d1/share");
    const user = userEvent.setup();
    expect(await screen.findByText("Deck not found.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Share deck" })).toBeInTheDocument();
    await screen.findAllByRole("row");
    await user.click(screen.getByRole("button", { name: "Create link" }));
    await user.click(screen.getByRole("checkbox", { name: /Allow copying the link created/ }));
    await user.click(screen.getByRole("button", { name: "Revoke" }));
    await user.click(screen.getByRole("button", { name: "Revoke link" }));
    await waitFor(() => {
      expect(screen.getAllByRole("alert").map((a) => a.textContent)).toEqual([
        "Deck not found.",
        "Something went wrong on the server. Try again.",
        "Something went wrong on the server. Try again.",
        "Already revoked.",
      ]);
    });
  });

  it("shows an empty list, a list that cannot load and a copy that fails", async () => {
    mockApi({
      ...me,
      "GET /decks/d1": { body: DECK },
      "GET /decks/d1/shares": { body: [] },
      "POST /decks/d1/shares": { status: 201, body: { ...link("s2"), token: "t" } },
    });
    const { unmount } = renderApp("/decks/d1/share");
    const user = userEvent.setup();
    expect(await screen.findByText("No links yet.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Create link" }));
    expect(await screen.findByText(/can preview the deck/)).toBeInTheDocument();
    vi.spyOn(navigator.clipboard, "writeText").mockRejectedValue(new Error("denied"));
    await user.click(screen.getByRole("button", { name: "Copy" }));
    expect(await screen.findByText(/Could not copy/)).toBeInTheDocument();
    unmount();
    mockApi({ ...me, "GET /decks/d1": { body: DECK }, "GET /decks/d1/shares": { status: 500 } });
    renderApp("/decks/d1/share");
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong");
  });

  it("lets the owner choose how long a link works and shows when links expire", async () => {
    const calls = mockApi({
      ...me,
      "GET /decks/d1": { body: DECK },
      "GET /decks/d1/shares": {
        body: [
          link("live", { expires_at: "2999-01-01T00:00:00Z" }),
          link("old", { expires_at: "2026-11-02T10:00:00Z" }),
          link("forever"),
        ],
      },
      "POST /decks/d1/shares": (call) => {
        const days = (call.body as { expires_in_days: number | null }).expires_in_days;
        return {
          status: 201,
          body: { ...link("new", { expires_at: days === null ? null : "2999-02-01T00:00:00Z" }), token: "t" },
        };
      },
    });
    renderApp("/decks/d1/share");
    const user = userEvent.setup();
    const rows = await screen.findAllByRole("row");
    expect(within(rows[1] as HTMLElement).getByText(/^Active until/)).toBeInTheDocument();
    expect(within(rows[2] as HTMLElement).getByText(/^Expired/)).toBeInTheDocument();
    expect(within(rows[2] as HTMLElement).getByRole("checkbox")).toBeDisabled();
    expect(within(rows[2] as HTMLElement).queryByRole("button", { name: "Revoke" })).toBeNull();
    expect(within(rows[3] as HTMLElement).getByText("Active, never expires")).toBeInTheDocument();

    const picker = screen.getByRole("combobox", { name: "Link works for" });
    expect(picker).toHaveValue("30");
    await user.selectOptions(picker, "7");
    await user.click(screen.getByRole("button", { name: "Create link" }));
    expect(await screen.findByText(/the deck until/)).toBeInTheDocument();
    await user.selectOptions(picker, "Never expires");
    await user.click(screen.getByRole("button", { name: "Create link" }));
    await waitFor(() => {
      expect(calls.filter((c) => c.method === "POST").map((c) => c.body)).toEqual([
        { allow_clone: false, expires_in_days: 7 },
        { allow_clone: false, expires_in_days: null },
      ]);
    });
    expect(await screen.findByText(/can preview the deck\.$/)).toBeInTheDocument();
  });

  it("is reached from the deck page", async () => {
    mockApi({ ...me, "GET /decks": { body: [DECK] }, "GET /decks/d1/stats": { status: 500 } });
    renderApp("/decks/d1");
    expect(await screen.findByRole("link", { name: "Share" })).toHaveAttribute("href", "/decks/d1/share");
  });
});

const SHARED: SharedDeck = {
  name: "Spanish",
  decks: [
    { name: "Spanish", depth: 0 },
    { name: "Verbs", depth: 1 },
  ],
  deck_count: 2,
  notes: [
    { deck_index: 0, note_type: "basic", fields: { front: "**hola**", back: "hello" } },
    { deck_index: 1, note_type: "cloze", fields: { text: "{{c1::ser}} is to be" } },
    { deck_index: 1, note_type: "custom", fields: { Word: "gato", Meaning: '<img src="x" onerror="alert(1)">cat' } },
    { deck_index: 9, note_type: "image_occlusion", fields: { header: "Map", back_extra: "" } },
  ],
  note_count: 10,
  allow_clone: true,
};

describe("shared deck page", () => {
  it("previews the shared deck through the sanitizer and copies it", async () => {
    const calls = mockApi({
      ...me,
      "GET /shared/abc": { body: SHARED },
      "POST /shared/abc/clone": { status: 201, body: { ...DECK, id: "d9", name: "Spanish" } },
      "GET /decks": { body: [{ ...DECK, id: "d9", name: "Spanish" }] },
      "GET /decks/d9/stats": { status: 500 },
    });
    const { container } = renderApp("/shared#token=abc");
    const user = userEvent.setup();
    expect(await screen.findByRole("heading", { name: "Spanish" })).toBeInTheDocument();
    await waitFor(() => {
      expect(document.title).toBe("Spanish · Flash-Cards");
    });
    expect(screen.getByText("10 notes in 2 decks.")).toBeInTheDocument();
    expect(screen.getByText("Showing the first 4 of 10.")).toBeInTheDocument();
    expect(screen.getByText("hola").tagName).toBe("STRONG");
    expect(screen.getByText("[…]")).toBeInTheDocument();
    expect(screen.getByText("gato")).toBeInTheDocument();
    expect(container.querySelector("[onerror]")).toBeNull();
    expect(screen.getAllByText("Verbs")[0]).toHaveStyle({ marginLeft: "16px" });
    await user.click(screen.getByRole("button", { name: "Copy to my decks" }));
    // The copy opens as a deck of its own.
    expect(await screen.findByRole("link", { name: "Study now" })).toHaveAttribute("href", "/decks/d9/study");
    expect(screen.getByRole("heading", { name: "Spanish", level: 1 })).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST" && c.path === "/shared/abc/clone")).toBe(true);
  });

  it("says when copying is not allowed, and when a deck has one level and no notes", async () => {
    mockApi({
      ...me,
      "GET /shared/abc": {
        body: {
          ...SHARED,
          decks: [{ name: "Solo", depth: 0 }],
          deck_count: 1,
          notes: [],
          note_count: 0,
          allow_clone: false,
        },
      },
    });
    renderApp("/shared#token=abc");
    expect(await screen.findByText("The owner has not allowed copying this deck.")).toBeInTheDocument();
    expect(screen.getByText("0 notes in 1 deck.")).toBeInTheDocument();
    expect(screen.getByText("This deck has no notes yet.")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Decks" })).not.toBeInTheDocument();
  });

  it("explains an incomplete or revoked link and a copy that fails", async () => {
    mockApi({ ...me });
    const { unmount } = renderApp("/shared");
    expect(await screen.findByText(/This link is incomplete/)).toBeInTheDocument();
    unmount();
    mockApi({ ...me, "GET /shared/gone": { status: 404, body: { detail: "Not found." } } });
    const second = renderApp("/shared#token=gone");
    expect(await screen.findByText("The link may have been revoked.")).toBeInTheDocument();
    second.unmount();
    mockApi({
      ...me,
      "GET /shared/abc": { body: { ...SHARED, note_count: 1, notes: [SHARED.notes[0]], deck_count: 1 } },
      "POST /shared/abc/clone": { status: 413, body: { detail: "That deck is too large to copy." } },
    });
    renderApp("/shared#token=abc");
    const user = userEvent.setup();
    expect(await screen.findByText("1 note in 1 deck.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Copy to my decks" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("That deck is too large to copy.");
  });

  it("keeps the link through sign-in", async () => {
    mockApi({
      "GET /auth/me": { status: 401, body: { detail: "Not authenticated." } },
      "POST /auth/login": { body: USER },
      "GET /shared/abc": { body: SHARED },
    });
    renderApp("/shared#token=abc");
    const user = userEvent.setup();
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    await user.type(screen.getByLabelText("Email"), "ada@example.com");
    await user.type(screen.getByLabelText("Password"), "correct horse battery");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("heading", { name: "Spanish" })).toBeInTheDocument();
  });
});
