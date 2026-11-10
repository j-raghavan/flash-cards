// SPDX-License-Identifier: AGPL-3.0-only
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { exportUrl, importApkg } from "../api/ankiRoundTrip";
import { USER, mockApi, renderApp } from "../test/helpers";
import { parseDeckNames } from "./ImportPage";

const me = { "GET /auth/me": { body: USER } };

const REPORT = {
  decks_created: 2,
  notes_imported: 40,
  notes_skipped: 1,
  notes_not_selected: 5,
  cards_scheduled: 30,
  reviews_imported: 250,
  unsupported: [
    { kind: "media", count: 3, examples: [] },
    { kind: "unknown_note_type", count: 1, examples: ["Basic (type in)"] },
  ],
};

const apkg = (size = 3): File => {
  const file = new File(["x".repeat(size)], "spanish.apkg", { type: "application/octet-stream" });
  return file;
};

describe("anki round-trip client", () => {
  it("sends the selection and dry-run flag as form fields, and builds export links", async () => {
    const append = vi.spyOn(FormData.prototype, "append");
    mockApi({ "POST /import/apkg": { body: REPORT } });
    await importApkg(apkg(), { dryRun: true, decks: ["A::B", "C"], tags: ["t1"] });
    expect(append.mock.calls.map(([name, value]) => [name, typeof value === "string" ? value : "file"])).toEqual([
      ["file", "file"],
      ["dry_run", "true"],
      ["deck", "A::B"],
      ["deck", "C"],
      ["tag", "t1"],
    ]);
    expect(exportUrl("d 1", "csv")).toBe("/api/decks/d%201/export?format=csv");
    expect(parseDeckNames(" Languages::Spanish \n\nC\nC")).toEqual(["Languages::Spanish", "C"]);
  });
});

describe("import wizard", () => {
  it("previews with a dry run, then imports", async () => {
    const calls = mockApi({
      ...me,
      "POST /import/apkg": (call) => {
        const dry = (call.body as { dry_run: string }).dry_run === "true";
        return { status: dry ? 200 : 201, body: { ...REPORT, dry_run: dry } };
      },
      "GET /decks": { body: [] },
    });
    renderApp("/import");
    const user = userEvent.setup();
    expect(await screen.findByRole("heading", { name: "Import from Anki" })).toBeInTheDocument();
    await waitFor(() => {
      expect(document.title).toBe("Import from Anki · Flash-Cards");
    });
    const preview = screen.getByRole("button", { name: "Preview import" });
    expect(preview).toBeDisabled();
    await user.upload(screen.getByLabelText(/Anki package/), apkg());
    await user.click(screen.getByText("Import only some decks or tags"));
    await user.type(screen.getByLabelText("Decks, one per line"), "Languages::Spanish");
    await user.type(screen.getByLabelText("Tags, separated by spaces"), "verbs");
    await user.click(preview);

    const heading = await screen.findByRole("heading", { name: "Preview of spanish.apkg" });
    await waitFor(() => {
      expect(heading).toHaveFocus();
    });
    expect(screen.getByText("Check the preview")).toHaveAttribute("aria-current", "step");
    expect(screen.getByText("Notes to import").nextSibling).toHaveTextContent("40");
    expect(screen.getByText("Past reviews to import")).toBeInTheDocument();
    expect(screen.getByText(/Media files, which are not imported: 3/)).toBeInTheDocument();
    expect(screen.getByText("(for example Basic (type in))")).toBeInTheDocument();
    expect(calls[calls.length - 1]?.body).toMatchObject({ dry_run: "true", deck: "Languages::Spanish", tag: "verbs" });

    await user.click(screen.getByRole("button", { name: "Import" }));
    const finished = await screen.findByRole("heading", { name: "Import finished" });
    await waitFor(() => {
      expect(finished).toHaveFocus();
    });
    expect(screen.getByText("Notes imported").nextSibling).toHaveTextContent("40");
    expect(calls[calls.length - 1]?.body).toMatchObject({ dry_run: "false" });
    await user.click(screen.getByRole("button", { name: "Import another file" }));
    expect(screen.getByRole("button", { name: "Preview import" })).toBeDisabled();
    expect(screen.getByRole("heading", { name: "Import from Anki" })).toHaveFocus();
  });

  it("treats an answer without a dry run as a finished import", async () => {
    mockApi({
      ...me,
      "POST /import/apkg": { status: 201, body: { decks_created: 1, notes_imported: 3, notes_skipped: 0 } },
    });
    renderApp("/import");
    const user = userEvent.setup();
    await user.upload(await screen.findByLabelText(/Anki package/), apkg());
    await user.click(screen.getByRole("button", { name: "Preview import" }));
    expect(await screen.findByRole("heading", { name: "Import finished" })).toBeInTheDocument();
    expect(screen.queryByText("Notes not selected")).not.toBeInTheDocument();
    expect(screen.queryByText("Not carried over")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to your decks" })).toHaveAttribute("href", "/");
  });

  it("refuses a file that is too large or a selection that is too long, and reports server errors", async () => {
    mockApi({
      ...me,
      "POST /import/apkg": (call) =>
        (call.body as { dry_run: string }).dry_run === "true"
          ? { body: { ...REPORT, unsupported: [], dry_run: true } }
          : { status: 422, body: { detail: "This package needs a newer reader." } },
    });
    renderApp("/import");
    const user = userEvent.setup();
    const input = await screen.findByLabelText(/Anki package/);
    const big = apkg();
    Object.defineProperty(big, "size", { value: 41 * 1024 * 1024 });
    await user.upload(input, big);
    await user.click(screen.getByRole("button", { name: "Preview import" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("larger than 40 MB");

    await user.upload(input, apkg());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await user.click(screen.getByText("Import only some decks or tags"));
    fireEvent.change(screen.getByLabelText("Tags, separated by spaces"), {
      target: { value: Array.from({ length: 51 }, (_, i) => `t${String(i)}`).join(" ") },
    });
    await user.click(screen.getByRole("button", { name: "Preview import" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Choose at most 50 decks");
    fireEvent.change(screen.getByLabelText("Tags, separated by spaces"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Decks, one per line"), { target: { value: "D".repeat(201) } });
    await user.click(screen.getByRole("button", { name: "Preview import" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Choose at most 50 decks");
    fireEvent.change(screen.getByLabelText("Decks, one per line"), { target: { value: "" } });

    await user.click(screen.getByRole("button", { name: "Preview import" }));
    await screen.findByRole("heading", { name: "Preview of spanish.apkg" });
    expect(screen.queryByText("Not carried over")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Import" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This package needs a newer reader.");
    await user.click(screen.getByRole("button", { name: "Start over" }));
    expect(screen.getByRole("button", { name: "Preview import" })).toBeDisabled();
    expect(screen.getByRole("heading", { name: "Import from Anki" })).toHaveFocus();
    fireEvent.submit(screen.getByRole("form", { name: "Choose a package" }));
    await waitFor(() => {
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });
  });

  it("is reached from the deck list, and the deck page offers exports", async () => {
    const deck = { id: "d1", name: "Bio", parent_id: null, created_at: "2026-01-01T00:00:00Z" };
    mockApi({ ...me, "GET /decks": { body: [deck] }, "GET /decks/d1/stats": { status: 500 } });
    const { unmount } = renderApp("/");
    expect(await screen.findByRole("link", { name: "Import decks from Anki" })).toHaveAttribute("href", "/import");
    unmount();
  });

  it("exports a deck as a download, saying what was left out or why it failed", async () => {
    const deck = { id: "d1", name: "Bio", parent_id: null, created_at: "2026-01-01T00:00:00Z" };
    mockApi({ ...me, "GET /decks": { body: [deck] }, "GET /decks/d1/stats": { status: 500 } });
    const api = globalThis.fetch;
    const replies: (Response | Error)[] = [
      new Response("apkg-bytes", {
        status: 200,
        headers: { "Content-Disposition": 'attachment; filename="Bio.apkg"', "X-Omitted-Notes": "2" },
      }),
      new Response("{}", { status: 200 }),
      new Response(JSON.stringify({ detail: "A deck export holds at most 200000 cards." }), { status: 413 }),
      new Response("not json", { status: 502 }),
      new TypeError("offline"),
    ];
    const exportCalls: string[] = [];
    vi.stubGlobal("fetch", (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input instanceof Request ? input.url : input.toString();
      if (!url.includes("/export")) {
        return api(input, init);
      }
      exportCalls.push(url);
      const reply = replies.shift() as Response | Error;
      return reply instanceof Error ? Promise.reject(reply) : Promise.resolve(reply);
    });
    const saved: string[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      saved.push(this.download);
    });
    URL.createObjectURL = vi.fn(() => "blob:export");
    URL.revokeObjectURL = vi.fn();
    renderApp("/decks/d1");
    const user = userEvent.setup();
    const group = await screen.findByRole("group", { name: "Export this deck" });
    await user.click(within(group).getByRole("button", { name: "Anki package" }));
    expect(
      await screen.findByText(/2 custom or image occlusion notes were left out; JSON keeps them/),
    ).toBeInTheDocument();
    await user.click(within(group).getByRole("button", { name: "JSON" }));
    expect(await screen.findByText("Downloaded.")).toBeInTheDocument();
    expect(saved).toEqual(["Bio.apkg", "deck.json"]);
    await user.click(within(group).getByRole("button", { name: "CSV" }));
    expect(await screen.findByText("A deck export holds at most 200000 cards.")).toBeInTheDocument();
    await user.click(within(group).getByRole("button", { name: "CSV" }));
    await waitFor(() => {
      expect(screen.getAllByText("Something went wrong on the server. Try again.")).toHaveLength(2); // with the statistics
    });
    await user.click(within(group).getByRole("button", { name: "CSV" }));
    expect(await screen.findByText(/Could not reach the server/)).toBeInTheDocument();
    expect(exportCalls[0]).toBe("/api/decks/d1/export?format=apkg");
  });
});
