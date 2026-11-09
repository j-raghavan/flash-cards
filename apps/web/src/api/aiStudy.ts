// SPDX-License-Identifier: AGPL-3.0-only
// The AI study routes: mock exams (F-AI-7), chat with a deck's source documents
// (F-AI-8), deck summaries (F-AI-6) and typed-answer evaluation (F-STUDY-2).
// Everything these return is model output, so it is untrusted: callers render
// text through CardContent, which sanitizes it.

import { ApiError, describeError, request } from "./client";
import type { Rating } from "./types";

const id = encodeURIComponent;

// --- mock exams ---

export interface ExamQuestion {
  id: string;
  prompt: string;
  topic: string;
}

export interface MockExam {
  id: string;
  deck_id: string;
  created_at: string;
  time_limit_seconds: number | null;
  /** When the timer runs out, or null for an untimed exam. */
  deadline: string | null;
  submitted_at: string | null;
  questions: ExamQuestion[];
}

export interface TopicScore {
  topic: string;
  /** 0 to 1. */
  score: number;
  question_count: number;
}

export interface ExamResult {
  overall_score: number;
  topics: TopicScore[];
  question_scores: { question_id: string; score: number }[];
}

export const MAX_EXAM_QUESTIONS = 50;

export const mockExams = {
  create: (deckId: string, questionCount: number, timeLimitSeconds: number | null) =>
    request<MockExam>(`/decks/${id(deckId)}/mock-exam`, {
      method: "POST",
      json: { question_count: questionCount, time_limit_seconds: timeLimitSeconds },
    }),
  submit: (examId: string, answers: { question_id: string; answer: string }[]) =>
    request<ExamResult>(`/mock-exams/${id(examId)}/submit`, { method: "POST", json: { answers } }),
};

// --- document chat ---

export interface Citation {
  file_id: string;
  page: number;
  /** The uploaded file's name (the API caps it at 120 characters), or null when not known. */
  file_name?: string | null;
}

export interface ChatAnswer {
  answer: string;
  citations: Citation[];
}

export const deckChat = {
  ask: (deckId: string, question: string, signal: AbortSignal) =>
    request<ChatAnswer>(`/decks/${id(deckId)}/chat`, { method: "POST", json: { question }, signal }),
};

// --- deck summary ---

export interface DeckSummary {
  summary: string;
  key_points: string[];
}

export const summarizeDeck = (deckId: string, signal: AbortSignal) =>
  request<DeckSummary>(`/decks/${id(deckId)}/summary`, { method: "POST", signal });

// --- typed-answer evaluation ---

export type Verdict = "correct" | "partial" | "incorrect";

export interface Evaluation {
  verdict: Verdict;
  /** 0 to 1. */
  score: number;
  explanation: string;
}

export const evaluateAnswer = (cardId: string, answer: string, signal: AbortSignal) =>
  request<Evaluation>(`/cards/${id(cardId)}/evaluate`, { method: "POST", json: { answer }, signal });

export const VERDICT_LABELS: Record<Verdict, string> = {
  correct: "Correct",
  partial: "Partly correct",
  incorrect: "Not correct",
};

/** The rating an evaluation suggests; the student confirms or changes it. */
export function suggestedRating(evaluation: Evaluation): Rating {
  if (evaluation.verdict === "correct") {
    return evaluation.score >= 0.95 ? 4 : 3;
  }
  return evaluation.verdict === "partial" ? 2 : 1;
}

// --- errors ---

/** A percentage for a 0 to 1 score, whole numbers only. */
export function percent(score: number): string {
  return `${String(Math.round(Math.max(0, Math.min(1, score)) * 100))}%`;
}

/**
 * A message for a failed AI call. Quota and budget refusals (429), output the
 * server could not use (502) and an unavailable provider (503) each say what
 * to do next; anything else keeps the server's own message.
 */
export function aiErrorMessage(failure: unknown): string {
  if (failure instanceof ApiError) {
    if (failure.status === 429) {
      return `${failure.message} Everything else keeps working; AI features are available again when the limit resets.`;
    }
    if (failure.status === 502) {
      return "The AI answered with something that could not be used, so nothing was saved. Try again.";
    }
    if (failure.status === 503) {
      return `${failure.message} Try again in a moment.`;
    }
  }
  return describeError(failure);
}

export interface Unlock {
  required: number;
  have: number;
}

/**
 * The card threshold a 409 refusal names ("needs at least 20 cards; the deck
 * has 5"), or null when the failure is something else.
 */
export function unlockThreshold(failure: unknown): Unlock | null {
  if (!(failure instanceof ApiError) || failure.status !== 409) {
    return null;
  }
  const match = /at least (\d+) cards; the deck has (\d+)/.exec(failure.message);
  if (match === null) {
    return null;
  }
  return { required: Number(match[1]), have: Number(match[2]) };
}

/** True for a request the user's own navigation cancelled. */
export function isAbort(failure: unknown): boolean {
  return failure instanceof DOMException && failure.name === "AbortError";
}
