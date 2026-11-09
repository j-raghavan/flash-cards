// SPDX-License-Identifier: AGPL-3.0-only
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ExamResult, MockExam } from "../api/aiStudy";
import { USER, mockApi, renderApp } from "../test/helpers";
import { timeLeftText } from "./MockExamPage";

const me = { "GET /auth/me": { body: USER } };

const exam = (over: Partial<MockExam> = {}): MockExam => ({
  id: "e1",
  deck_id: "d1",
  created_at: "2026-01-01T10:00:00Z",
  time_limit_seconds: null,
  deadline: null,
  submitted_at: null,
  questions: [
    { id: "q1", prompt: "What is **ATP**? <img src=x onerror=alert(1)>", topic: "Cells <script>alert(1)</script>" },
    { id: "q2", prompt: "Define osmosis.", topic: "Transport" },
  ],
  ...over,
});

const RESULT: ExamResult = {
  overall_score: 0.4,
  topics: [
    { topic: "Cells <b onclick=alert(1)>x</b>", score: 0.8, question_count: 1 },
    { topic: "Transport", score: 0, question_count: 1 },
  ],
  question_scores: [{ question_id: "q1", score: 0.8 }],
};

afterEach(() => {
  vi.useRealTimers();
});

async function startExam(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await user.click(await screen.findByRole("button", { name: "Start the exam" }));
}

describe("mock exam", () => {
  it("is reached from the deck page", async () => {
    mockApi({
      ...me,
      "GET /decks": { body: [{ id: "d1", name: "Biology", parent_id: null, created_at: "2026-01-01T00:00:00Z" }] },
      "GET /decks/d1/stats": { status: 500 },
    });
    renderApp("/decks/d1");
    expect(await screen.findByRole("link", { name: "Mock exam" })).toHaveAttribute("href", "/decks/d1/exam");
  });

  it("composes an exam, sanitizes its questions, and shows the graded result by topic", async () => {
    const calls = mockApi({
      ...me,
      "POST /decks/d1/mock-exam": { status: 201, body: exam() },
      "POST /mock-exams/e1/submit": { body: RESULT },
    });
    renderApp("/decks/d1/exam");
    const user = userEvent.setup();
    const count = await screen.findByLabelText(/Number of questions/);
    await user.clear(count);
    await user.type(count, "2");
    await startExam(user);

    const heading = await screen.findByRole("heading", { name: "2 questions" });
    expect(heading).toHaveFocus();
    expect(calls.find((c) => c.path === "/decks/d1/mock-exam")?.body).toEqual({
      question_count: 2,
      time_limit_seconds: null,
    });
    expect(screen.getByText(/AI-generated questions/)).toBeInTheDocument();
    expect(screen.queryByRole("timer")).not.toBeInTheDocument();
    expect(screen.getByText("ATP")).toBeInTheDocument();
    expect(document.querySelector("img[onerror]")).toBeNull();
    expect(document.querySelector("main script")).toBeNull();
    expect(screen.getByText("2 unanswered questions will score zero.")).toBeInTheDocument();

    await user.type(screen.getByLabelText("Answer to question 1"), "  The energy currency  ");
    expect(screen.getByText("1 unanswered question will score zero.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Submit for grading" }));

    const score = await screen.findByRole("heading", { name: "Your score: 40%" });
    expect(score).toHaveFocus();
    expect(calls.find((c) => c.path === "/mock-exams/e1/submit")?.body).toEqual({
      answers: [{ question_id: "q1", answer: "The energy currency" }],
    });
    expect(screen.getByText(/Graded by the AI/)).toBeInTheDocument();
    const table = screen.getByRole("table");
    expect(within(table).getByText("80%")).toBeInTheDocument();
    expect(table.querySelector("[onclick]")).toBeNull();
    expect(screen.getByText("The energy currency")).toBeInTheDocument();
    expect(screen.getByText("No answer")).toBeInTheDocument();
    expect(screen.getByText("Score: 0%")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Study this deck" })).toHaveAttribute("href", "/decks/d1/study");

    await user.click(screen.getByRole("button", { name: "Take another exam" }));
    expect(screen.getByRole("button", { name: "Start the exam" })).toBeInTheDocument();
  });

  it("checks the setup before composing", async () => {
    const calls = mockApi({ ...me, "POST /decks/d1/mock-exam": { status: 201, body: exam() } });
    renderApp("/decks/d1/exam");
    const user = userEvent.setup();
    const count = await screen.findByLabelText(/Number of questions/);
    await user.clear(count);
    await user.type(count, "51");
    expect(screen.getByRole("button", { name: "Start the exam" })).toBeDisabled();
    await user.clear(count);
    await user.type(count, "5");
    await user.click(screen.getByLabelText("Time the exam"));
    const minutes = screen.getByLabelText("Time limit in minutes");
    await user.clear(minutes);
    expect(screen.getByRole("button", { name: "Start the exam" })).toBeDisabled();
    await user.type(minutes, "15");
    await startExam(user);
    await screen.findByRole("heading", { name: "2 questions" });
    expect(calls.find((c) => c.path === "/decks/d1/mock-exam")?.body).toEqual({
      question_count: 5,
      time_limit_seconds: 900,
    });
  });

  it("composes once however often Start is pressed, keeping focus on the button", async () => {
    let release: (() => void) | null = null;
    const calls = mockApi({
      ...me,
      "POST /decks/d1/mock-exam": () =>
        new Promise((resolve) => {
          release = () => {
            resolve({ status: 201, body: exam() });
          };
        }),
    });
    renderApp("/decks/d1/exam");
    const user = userEvent.setup();
    await startExam(user);
    const busy = screen.getByRole("button", { name: "Composing your exam…" });
    expect(busy).toHaveFocus();
    expect(screen.getByText("Composing questions. This can take up to a minute.")).toBeInTheDocument();
    await user.click(busy);
    expect(calls.filter((c) => c.path === "/decks/d1/mock-exam")).toHaveLength(1);
    (release as unknown as () => void)();
    expect(await screen.findByRole("heading", { name: "2 questions" })).toBeInTheDocument();
  });

  it("shows how far the deck is from unlocking mock exams", async () => {
    mockApi({
      ...me,
      "POST /decks/d1/mock-exam": {
        status: 409,
        body: { detail: "a mock exam needs at least 20 cards; the deck has 5" },
      },
    });
    renderApp("/decks/d1/exam");
    const user = userEvent.setup();
    await startExam(user);
    expect(
      await screen.findByText(/Mock exams unlock once a deck has 20 cards. This deck has 5, so add 15 more./),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Add a card" })).toHaveAttribute("href", "/decks/d1/notes/new");
  });

  it("explains quota, unusable output and unavailable AI", async () => {
    let reply = 0;
    const replies = [
      { status: 429, body: { detail: "Daily AI request limit reached." } },
      { status: 502, body: { detail: "The AI returned an unusable answer; try again." } },
      { status: 503, body: { detail: "AI is unavailable right now." } },
      { status: 409, body: { detail: "Something else." } },
    ];
    mockApi({ ...me, "POST /decks/d1/mock-exam": () => replies[reply++] ?? { status: 500 } });
    renderApp("/decks/d1/exam");
    const user = userEvent.setup();
    await startExam(user);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Daily AI request limit reached. Everything else keeps working",
    );
    await startExam(user);
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("could not be used, so nothing was saved");
    });
    await startExam(user);
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("AI is unavailable right now. Try again in a moment.");
    });
    await startExam(user);
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("Something else.");
    });
  });

  it("keeps the answers when grading fails, and stops once the exam is closed", async () => {
    let attempt = 0;
    mockApi({
      ...me,
      "POST /decks/d1/mock-exam": { status: 201, body: exam({ questions: [{ id: "q1", prompt: "Q?", topic: "T" }] }) },
      "POST /mock-exams/e1/submit": () => {
        attempt += 1;
        return attempt === 1
          ? { status: 502, body: { detail: "x" } }
          : { status: 409, body: { detail: "this exam was already submitted" } };
      },
    });
    renderApp("/decks/d1/exam");
    const user = userEvent.setup();
    await startExam(user);
    await user.type(await screen.findByLabelText("Answer to question 1"), "kept");
    await user.click(screen.getByRole("button", { name: "Submit for grading" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("could not be used");
    expect(screen.getByLabelText("Answer to question 1")).toHaveValue("kept");
    await user.click(screen.getByRole("button", { name: "Submit for grading" }));
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("This exam can no longer be submitted");
    });
    expect(screen.queryByRole("button", { name: "Submit for grading" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Answer to question 1")).toHaveAttribute("readonly");
    await user.click(screen.getByRole("button", { name: "Take another exam" }));
    expect(screen.getByRole("button", { name: "Start the exam" })).toBeInTheDocument();
  });

  it("shows a calm timer and sends the answers when time runs out", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const calls = mockApi({
      ...me,
      "POST /decks/d1/mock-exam": {
        status: 201,
        body: exam({
          time_limit_seconds: 360,
          // The server's clock is an hour ahead: only the limit counts.
          created_at: "2026-01-01T11:00:00Z",
          deadline: "2026-01-01T11:06:00Z",
        }),
      },
      "POST /mock-exams/e1/submit": { body: RESULT },
    });
    renderApp("/decks/d1/exam");
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime.bind(vi) });
    await user.click(await screen.findByLabelText("Time the exam"));
    await startExam(user);
    const timer = await screen.findByRole("timer", { name: "Time left" });
    expect(timer).toHaveTextContent("About 6 minutes left");
    await user.type(screen.getByLabelText("Answer to question 2"), "Water moves");
    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    expect(timer).toHaveTextContent("About 5 minutes left");
    expect(screen.getByText("Five minutes left.")).toBeInTheDocument();
    act(() => {
      vi.advanceTimersByTime(250_000);
    });
    expect(screen.getByText("One minute left.")).toBeInTheDocument();
    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    expect(await screen.findByRole("heading", { name: "Your score: 40%" })).toBeInTheDocument();
    const submits = calls.filter((c) => c.path === "/mock-exams/e1/submit");
    expect(submits).toHaveLength(1);
    expect(submits[0]?.body).toEqual({ answers: [{ question_id: "q2", answer: "Water moves" }] });
  });

  it("sends the answers once when time runs out during a submission", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let release: (() => void) | null = null;
    const calls = mockApi({
      ...me,
      "POST /decks/d1/mock-exam": {
        status: 201,
        body: exam({ time_limit_seconds: 60, deadline: "2026-01-01T10:01:00Z" }),
      },
      "POST /mock-exams/e1/submit": () =>
        new Promise((resolve) => {
          release = () => {
            resolve({ body: RESULT });
          };
        }),
    });
    renderApp("/decks/d1/exam");
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime.bind(vi) });
    await startExam(user);
    await user.click(await screen.findByRole("button", { name: "Submit for grading" }));
    expect(screen.getByText("The AI is grading your answers.")).toBeInTheDocument();
    act(() => {
      vi.advanceTimersByTime(61_000);
    });
    (release as unknown as () => void)();
    expect(await screen.findByRole("heading", { name: "Your score: 40%" })).toBeInTheDocument();
    expect(calls.filter((c) => c.path === "/mock-exams/e1/submit")).toHaveLength(1);
  });

  it("words the time left without counting seconds", () => {
    expect(timeLeftText(0)).toBe("Time is up");
    expect(timeLeftText(30_000)).toBe("Less than a minute left");
    expect(timeLeftText(60_000)).toBe("About 1 minute left");
    expect(timeLeftText(61_000)).toBe("About 2 minutes left");
  });
});
