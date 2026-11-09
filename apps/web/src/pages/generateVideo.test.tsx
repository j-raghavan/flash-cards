// SPDX-License-Identifier: AGPL-3.0-only
import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Job } from "../api/types";
import { FakeEventSource, USER, mockApi, renderApp } from "../test/helpers";

const me = { "GET /auth/me": { body: USER } };
const JOB: Job = {
  id: "j9",
  status: "queued",
  progress: 0,
  total_units: 1,
  completed_units: 0,
  cancel_requested: false,
  error: null,
  created_at: "2026-01-01T10:00:00Z",
  updated_at: "2026-01-01T10:00:00Z",
};
const NO_TRANSCRIPT = "No transcript is available for this video. Paste the transcript instead.";

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
});

describe("generate from a video or a transcript", () => {
  it("starts a job from a YouTube link", async () => {
    const calls = mockApi({ ...me, "POST /ai/generate/youtube": { status: 202, body: JOB }, "GET /ai/jobs/j9": { body: JOB } });
    renderApp("/decks/d1/generate");
    const user = userEvent.setup();
    const go = await screen.findByRole("button", { name: "Use the video's transcript" });
    expect(go).toBeDisabled();
    await user.type(screen.getByLabelText("YouTube link"), " https://youtu.be/dQw4w9WgXcQ ");
    await user.click(go);
    await waitFor(() => {
      expect(calls.some((c) => c.path === "/ai/jobs/j9")).toBe(true);
    });
    expect(calls.find((c) => c.path === "/ai/generate/youtube")?.body).toEqual({
      deck_id: "d1",
      url: "https://youtu.be/dQw4w9WgXcQ",
      thoroughness: "standard",
    });
    expect(calls.some((c) => c.path === "/ai/jobs/j9")).toBe(true);
  });

  it("points at the paste box when the video has no transcript", async () => {
    mockApi({ ...me, "POST /ai/generate/youtube": { status: 422, body: { detail: NO_TRANSCRIPT } } });
    renderApp("/decks/d1/generate");
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("YouTube link"), "https://youtu.be/dQw4w9WgXcQ");
    await user.click(screen.getByRole("button", { name: "Use the video's transcript" }));
    expect(await screen.findByText(NO_TRANSCRIPT)).toBeInTheDocument();
    expect(screen.getByLabelText("Or paste a transcript or lecture notes")).toHaveFocus();
  });

  it("keeps focus where it was for other failures", async () => {
    mockApi({
      ...me,
      "POST /ai/generate/youtube": { status: 422, body: { detail: "the transcript is too long (max 120000 chars)" } },
    });
    renderApp("/decks/d1/generate");
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("YouTube link"), "https://youtu.be/dQw4w9WgXcQ");
    await user.click(screen.getByRole("button", { name: "Use the video's transcript" }));
    expect(await screen.findByText(/too long/)).toBeInTheDocument();
    expect(screen.getByLabelText("Or paste a transcript or lecture notes")).not.toHaveFocus();
  });

  it("starts a job from a pasted transcript with the chosen thoroughness", async () => {
    const calls = mockApi({
      ...me,
      "POST /ai/generate/transcript": { status: 202, body: JOB },
      "GET /ai/jobs/j9": { body: JOB },
    });
    renderApp("/decks/d1/generate");
    const user = userEvent.setup();
    await user.click(await screen.findByLabelText("Thorough"));
    const go = screen.getByRole("button", { name: "Generate from this text" });
    expect(go).toBeDisabled();
    await user.type(screen.getByLabelText("Or paste a transcript or lecture notes"), "Mitochondria make ATP.");
    await user.click(go);
    await waitFor(() => {
      expect(calls.some((c) => c.path === "/ai/jobs/j9")).toBe(true);
    });
    expect(calls.find((c) => c.path === "/ai/generate/transcript")?.body).toEqual({
      deck_id: "d1",
      transcript: "Mitochondria make ATP.",
      thoroughness: "thorough",
    });
  });

  it("shows why a pasted transcript was refused", async () => {
    mockApi({ ...me, "POST /ai/generate/transcript": { status: 422, body: { detail: "the transcript is empty" } } });
    renderApp("/decks/d1/generate");
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Or paste a transcript or lecture notes"), "x");
    await user.click(screen.getByRole("button", { name: "Generate from this text" }));
    expect(await screen.findByText("the transcript is empty")).toBeInTheDocument();
  });

  it("refuses a paste over the limit instead of cutting it short", async () => {
    mockApi(me);
    renderApp("/decks/d1/generate");
    const box = await screen.findByLabelText("Or paste a transcript or lecture notes");
    fireEvent.change(box, { target: { value: "a".repeat(120_001) } });
    expect(screen.getByText(/the limit is 120,000/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Generate from this text" })).toBeDisabled();
  });
});
