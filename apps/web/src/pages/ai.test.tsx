// SPDX-License-Identifier: AGPL-3.0-only
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Draft, Job } from "../api/types";
import { FakeEventSource, USER, mockApi, renderApp } from "../test/helpers";

const me = { "GET /auth/me": { body: USER } };

const job = (over: Partial<Job> = {}): Job => ({
  id: "j1",
  status: "running",
  progress: 0.5,
  total_units: 4,
  completed_units: 2,
  cancel_requested: false,
  error: null,
  created_at: "2026-01-01T10:00:00Z",
  updated_at: "2026-01-01T10:00:00Z",
  ...over,
});

const DRAFTS: Draft[] = [
  { id: "x1", note_type: "basic", fields: { front: "What is $E$?", back: "Energy, see [notes](https://evil.example/b)" }, source_page: 1 },
  { id: "x2", note_type: "cloze", fields: { text: "{{c1::Paris}} is a city <https://evil.example/c>" }, source_page: 2 },
];

function lastSource(): FakeEventSource {
  const source = FakeEventSource.instances.at(-1);
  if (source === undefined) {
    throw new Error("no EventSource opened");
  }
  return source;
}

/**
 * The latest EventSource, once it exists. The job page opens it in an effect
 * that runs after the job is shown, so a test that has just seen the job on
 * screen waits for the stream rather than racing that effect.
 */
function openedSource(count = 1): Promise<FakeEventSource> {
  return waitFor(() => {
    expect(FakeEventSource.instances.length).toBeGreaterThanOrEqual(count);
    return lastSource();
  });
}

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
});

describe("generate page", () => {
  it("uploads a PDF with the chosen thoroughness and opens the job", async () => {
    const calls = mockApi({
      ...me,
      "POST /ai/generate": {
        status: 202,
        body: job({ status: "queued", progress: 0, total_units: 0, completed_units: 0 }),
      },
      "GET /ai/jobs/j1": { body: job({ status: "queued", progress: 0, total_units: 0, completed_units: 0 }) },
    });
    renderApp("/decks/d1/generate");
    const user = userEvent.setup();
    const submit = await screen.findByRole("button", { name: "Generate drafts" });
    expect(submit).toBeDisabled();
    await user.upload(screen.getByLabelText(/File/), new File(["%PDF"], "lecture.pdf", { type: "application/pdf" }));
    await user.click(screen.getByLabelText("Thorough"));
    await user.click(submit);
    expect(await screen.findByRole("heading", { name: "Card generation" })).toBeInTheDocument();
    expect(screen.getByText("Waiting to start…")).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/ai/generate")?.body).toMatchObject({
      deck_id: "d1",
      thoroughness: "thorough",
    });
    expect((await openedSource()).url).toBe("/api/ai/jobs/j1/events");
  });

  it("checks the file type and size before uploading, and shows server refusals", async () => {
    const calls = mockApi({
      ...me,
      "POST /ai/generate": { status: 429, body: { detail: "AI page quota exceeded for this period." } },
    });
    renderApp("/decks/d1/generate");
    const user = userEvent.setup({ applyAccept: false });
    const input = await screen.findByLabelText(/File/);
    await user.upload(input, new File(["x"], "notes.txt", { type: "text/plain" }));
    await user.click(screen.getByRole("button", { name: "Generate drafts" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Choose a PDF, PNG or JPEG file.");

    const big = new File(["x"], "big.png", { type: "image/png" });
    Object.defineProperty(big, "size", { value: 26 * 1024 * 1024 });
    await user.upload(input, big);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Generate drafts" }));
    expect(screen.getByRole("alert")).toHaveTextContent("larger than 25 MB");
    expect(calls.some((c) => c.path === "/ai/generate")).toBe(false);

    fireEvent.change(input, { target: { files: [] } });
    expect(screen.getByRole("button", { name: "Generate drafts" })).toBeDisabled();
    await user.upload(input, new File(["x"], "photo.jpg", { type: "image/jpeg" }));
    await user.click(screen.getByRole("button", { name: "Generate drafts" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("AI page quota exceeded");
  });

  it("states the page limit and the pages left, and shows the server's refusal", async () => {
    mockApi({
      ...me,
      "GET /account/ai-quota": {
        body: { period: "2026-11", monthly_pages: 10, used_pages: 9, max_document_pages: 300, quota_applies: true },
      },
      "POST /ai/generate": { status: 413, body: { detail: "This document has 412 pages; the limit is 300." } },
    });
    renderApp("/decks/d1/generate");
    expect(
      await screen.findByText(
        "PDFs up to 300 pages, 25 MB. You have 1 page of AI generation left this month; each page counts once.",
      ),
    ).toBeInTheDocument();
    const user = userEvent.setup();
    const input = screen.getByLabelText(/File/);
    expect(input).toHaveAccessibleDescription(/PDFs up to 300 pages/);
    await user.upload(input, new File(["%PDF"], "long.pdf", { type: "application/pdf" }));
    await user.click(screen.getByRole("button", { name: "Generate drafts" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This document has 412 pages; the limit is 300.");
  });

  it("tells an admin there is no page limit", async () => {
    mockApi({
      ...me,
      "GET /account/ai-quota": {
        body: { period: "2026-11", monthly_pages: 10, used_pages: 40, max_document_pages: null, quota_applies: false },
      },
    });
    renderApp("/decks/d1/generate");
    expect(
      await screen.findByText("PDFs of any length, up to 25 MB: as an admin you have no page limit."),
    ).toBeInTheDocument();
  });

  it("shows a generic size until the limits load, and never a negative allowance", async () => {
    mockApi({
      ...me,
      "GET /account/ai-quota": {
        body: { period: "2026-11", monthly_pages: 10, used_pages: 12, max_document_pages: 300, quota_applies: true },
      },
    });
    renderApp("/decks/d1/generate");
    expect(await screen.findByText("Files up to 25 MB.")).toBeInTheDocument();
    expect(
      await screen.findByText(
        "PDFs up to 300 pages, 25 MB. You have 0 pages of AI generation left this month; each page counts once.",
      ),
    ).toBeInTheDocument();
  });

  it("ignores a submit without a file and lists recent generations from the server", async () => {
    const started = "2026-01-01T10:00:00Z";
    const calls = mockApi({
      ...me,
      "GET /ai/jobs?deck_id=d1&limit=5": {
        body: [job({ id: "j9", status: "succeeded", created_at: started }), job({ id: "j8", status: "failed" })],
      },
    });
    renderApp("/decks/d1/generate");
    const when = new Date(started).toLocaleString();
    const links = await screen.findAllByRole("link", { name: when });
    expect(links[0]).toHaveAttribute("href", "/jobs/j9?deck=d1");
    expect(links[1]).toHaveAttribute("href", "/jobs/j8?deck=d1");
    expect(screen.getByText("Finished")).toBeInTheDocument();
    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(calls.some((c) => c.path === "/ai/jobs?deck_id=d1&limit=5")).toBe(true);
    const form = screen.getByRole("button", { name: "Generate drafts" }).closest("form");
    act(() => {
      form?.requestSubmit();
    });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("job page", () => {
  it("follows live progress until done, then approves the chosen drafts", async () => {
    let drafts = DRAFTS;
    const calls = mockApi({
      ...me,
      "GET /ai/jobs/j1": { body: job() },
      "GET /ai/jobs/j1/drafts": () => ({ body: drafts }),
      "POST /ai/jobs/j1/approve": (call) => {
        const ids = (call.body as { draft_ids: string[] }).draft_ids;
        drafts = drafts.filter((d) => !ids.includes(d.id));
        return { status: 201, body: ids.map((id) => ({ id: `card-${id}`, deck_id: "d1" })) };
      },
    });
    renderApp("/jobs/j1?deck=d1");
    expect(await screen.findByText(/2 of 4 pages\./)).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Generating cards…");
    expect(screen.getByRole("progressbar", { name: "Generation progress" })).toHaveAttribute("value", "50");

    const source = await openedSource();
    act(() => {
      source.emit("progress", JSON.stringify(job({ completed_units: 3, progress: 0.75 })));
      source.emit("progress", "not json");
      source.emit("progress", 42);
    });
    expect(await screen.findByText(/3 of 4 pages\./)).toBeInTheDocument();
    act(() => {
      source.emit("done", JSON.stringify(job({ status: "succeeded", completed_units: 4, progress: 1 })));
    });
    expect(source.readyState).toBe(FakeEventSource.CLOSED);
    expect(await screen.findByRole("heading", { name: "Review the drafts" })).toBeInTheDocument();
    expect(screen.getByText("AI draft, source page 2, cloze")).toBeInTheDocument();
    // Draft text is model output: its links show their address and cannot be clicked.
    const draftList = document.querySelector("ul.drafts") as HTMLElement;
    expect(draftList.querySelector("a")).toBeNull();
    expect(draftList).toHaveTextContent("Energy, see notes (https://evil.example/b)");
    expect(draftList).toHaveTextContent("is a city https://evil.example/c");
    expect(screen.getByText("Paris")).toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Untick all" }));
    expect(screen.getByRole("button", { name: "Approve 0 cards" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Tick all" }));
    await user.click(screen.getAllByRole("checkbox")[1] as HTMLElement);
    await user.click(screen.getAllByRole("checkbox")[0] as HTMLElement);
    await user.click(screen.getAllByRole("checkbox")[0] as HTMLElement);
    await user.click(screen.getByRole("button", { name: "Approve 1 card" }));
    expect(await screen.findByText(/Added 1 card\./)).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/ai/jobs/j1/approve")?.body).toEqual({ draft_ids: ["x1"] });

    await user.click(screen.getByRole("button", { name: "Approve 1 card" }));
    expect(await screen.findByText("Added 1 card. All drafts are reviewed.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to the deck" })).toHaveAttribute("href", "/decks/d1");
  });

  it("reads the job again when the stream closes, as it does for a finished job", async () => {
    let status: Job["status"] = "running";
    mockApi({
      ...me,
      "GET /ai/jobs/j1": () => ({ body: job({ status }) }),
      "GET /ai/jobs/j1/drafts": { body: [] },
    });
    renderApp("/jobs/j1");
    await screen.findByText(/Generating cards/);
    const source = await openedSource();
    status = "succeeded";
    act(() => {
      source.fail(FakeEventSource.CONNECTING); // the browser retries on its own
    });
    expect(screen.getByText(/Generating cards/)).toBeInTheDocument();
    act(() => {
      lastSource().fail(FakeEventSource.CLOSED);
    });
    expect(await screen.findByText("There are no drafts left to review.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to decks" })).toBeInTheDocument();
  });

  it("reconnects later when the stream closes on a job that is still running", async () => {
    let fail = false;
    mockApi({
      ...me,
      "GET /ai/jobs/j1": () => (fail ? { status: 500 } : { body: job() }),
    });
    const timers: (() => void)[] = [];
    const realSetTimeout = window.setTimeout.bind(window);
    vi.spyOn(window, "setTimeout").mockImplementation(((callback: () => void, delay?: number) => {
      if (delay === 3000) {
        timers.push(callback); // the reconnect, run by hand below
        return 0;
      }
      return realSetTimeout(callback, delay);
    }) as unknown as typeof window.setTimeout);
    renderApp("/jobs/j1");
    await screen.findByText(/2 of 4 pages/);
    const first = await openedSource();
    act(() => {
      first.fail(FakeEventSource.CLOSED);
    });
    await waitFor(() => {
      expect(timers).toHaveLength(1);
    });
    act(() => {
      timers[0]?.();
    });
    await waitFor(() => {
      expect(FakeEventSource.instances).toHaveLength(2);
    });
    fail = true;
    act(() => {
      lastSource().fail(FakeEventSource.CLOSED);
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong");
  });

  it("cancels a running job and retries a cancelled or failed one", async () => {
    const calls = mockApi({
      ...me,
      "GET /ai/jobs/j1": { body: job() },
      "POST /ai/jobs/j1/cancel": { body: job({ cancel_requested: true }) },
    });
    renderApp("/jobs/j1?deck=d1");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Cancel" }));
    expect(await screen.findByText(/Cancelling…/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
    const source = await openedSource();
    act(() => {
      source.emit("done", JSON.stringify(job({ status: "cancelled", cancel_requested: true })));
    });
    expect(await screen.findByText("Cancelled.")).toBeInTheDocument();

    mockApi({ "POST /ai/jobs/j1/retry": { body: job({ status: "queued" }) } });
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText(/Waiting to start/)).toBeInTheDocument();
    expect(calls.some((c) => c.path === "/ai/jobs/j1/cancel")).toBe(true);
  });

  it("shows why a job failed and a failed action", async () => {
    mockApi({
      ...me,
      "GET /ai/jobs/j1": { body: job({ status: "failed", error: "AI is unavailable right now." }) },
      "POST /ai/jobs/j1/retry": { status: 409, body: { detail: "The job cannot do that in its current state." } },
    });
    renderApp("/jobs/j1?deck=d1");
    expect(await screen.findByText("AI is unavailable right now.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to generation" })).toHaveAttribute("href", "/decks/d1/generate");
    expect(FakeEventSource.instances).toHaveLength(0);
    await userEvent.setup().click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("The job cannot do that in its current state.")).toBeInTheDocument();
  });

  it("shows a job or drafts that cannot load", async () => {
    mockApi({ ...me, "GET /ai/jobs/j1": { status: 404, body: { detail: "Not found." } } });
    renderApp("/jobs/j1");
    expect(await screen.findByRole("alert")).toHaveTextContent("Not found.");
  });

  it("shows drafts that cannot load and an approval that fails", async () => {
    let draftsFail = true;
    mockApi({
      ...me,
      "GET /ai/jobs/j1": { body: job({ status: "succeeded" }) },
      "GET /ai/jobs/j1/drafts": () => (draftsFail ? { status: 500 } : { body: DRAFTS }),
      "POST /ai/jobs/j1/approve": { status: 404, body: { detail: "Not found." } },
    });
    const { unmount } = renderApp("/jobs/j1");
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong");
    unmount();
    draftsFail = false;
    renderApp("/jobs/j1");
    await userEvent.setup().click(await screen.findByRole("button", { name: "Approve 2 cards" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Not found.");
  });

  it("stays on the job when approval creates no cards", async () => {
    mockApi({
      ...me,
      "GET /ai/jobs/j1": { body: job({ status: "succeeded" }) },
      "GET /ai/jobs/j1/drafts": { body: DRAFTS },
      "POST /ai/jobs/j1/approve": { status: 201, body: [] },
    });
    renderApp("/jobs/j1");
    await userEvent.setup().click(await screen.findByRole("button", { name: "Approve 2 cards" }));
    expect(await screen.findByText(/Added 0 cards\./)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Go to the deck" })).not.toBeInTheDocument();
  });

  it("works without EventSource support", async () => {
    vi.stubGlobal("EventSource", undefined);
    mockApi({ ...me, "GET /ai/jobs/j1": { body: job() } });
    renderApp("/jobs/j1");
    expect(await screen.findByText(/Generating cards/)).toBeInTheDocument();
    await waitFor(() => {
      expect(FakeEventSource.instances).toHaveLength(0);
    });
  });
});
