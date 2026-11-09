// SPDX-License-Identifier: AGPL-3.0-only
// Daily goal, streak, study statistics, replan diff and undo (ADR 0014).
// Shapes mirror the progress, goals and review routes of the API; everything a
// server without these routes does not send is optional, so the screens that
// use them degrade instead of breaking.

import { request } from "./client";
import type { CardSchedule, StudyPlan } from "./types";

const id = encodeURIComponent;

/** Today's count against the daily goal, as returned with every answer. */
export interface DailyGoal {
  date: string;
  time_zone: string;
  reviews: number;
  daily_goal: number;
  goal_met: boolean;
  /** True exactly when the latest answer reached the goal: the moment to celebrate. */
  just_completed: boolean;
}

export interface Today extends DailyGoal {
  streak: number;
}

/** The answer to POST /cards/{id}/review. */
export interface ReviewResult extends CardSchedule {
  review_id?: string;
  daily_goal?: DailyGoal;
}

export interface UndoResult {
  review_id: string;
  card_id: string;
  schedule: CardSchedule | null;
  daily_goal: DailyGoal;
}

export interface StudySettings {
  daily_goal: number;
  time_zone: string;
}

export interface HeatmapDay {
  date: string;
  reviews: number;
}

export interface RetentionPoint {
  week_start: string;
  reviews: number;
  recalled: number;
  rate: number | null;
}

export interface ForecastDay {
  date: string;
  due: number;
}

export interface ProgressStats {
  time_zone: string;
  deck_id: string | null;
  heatmap: HeatmapDay[];
  retention: RetentionPoint[];
  forecast: ForecastDay[];
}

export type ChangeReason =
  | "cards_added"
  | "cards_removed"
  | "cards_started"
  | "missed_days"
  | "days_passed"
  | "exam_date_changed"
  | "target_changed";

export interface PlanChange {
  since: string;
  changed: boolean;
  daily_load_before: number;
  daily_load_after: number;
  before: StudyPlan;
  reasons: { reason: ChangeReason; count: number }[];
}

/** A plan with what changed since it was last accepted; null for a goal's first plan. */
export interface PlanReport extends StudyPlan {
  changes?: PlanChange | null;
}

export const MIN_DAILY_GOAL = 1;
export const MAX_DAILY_GOAL = 1000;
export const MAX_HEATMAP_DAYS = 366;

export const progress = {
  today: () => request<Today>("/progress/today"),
  settings: () => request<StudySettings>("/progress/settings"),
  updateSettings: (changes: Partial<StudySettings>) =>
    request<StudySettings>("/progress/settings", { method: "PATCH", json: changes }),
  stats: (deckId: string | null, days: number, forecastDays: number) => {
    const params = new URLSearchParams({ days: String(days), forecast_days: String(forecastDays) });
    if (deckId !== null) {
      params.set("deck_id", deckId);
    }
    return request<ProgressStats>(`/progress/stats?${params.toString()}`);
  },
};

export const reviews = {
  undo: (reviewId: string) => request<UndoResult>(`/reviews/${id(reviewId)}/undo`, { method: "POST" }),
};

export const plans = {
  report: (goalId: string) => request<PlanReport>(`/goals/${id(goalId)}/plan`),
  accept: (goalId: string) => request<StudyPlan>(`/goals/${id(goalId)}/plan/accept`, { method: "POST" }),
};
