// SPDX-License-Identifier: AGPL-3.0-only
// Where the guided first run (F-ONB-1) stands, per user on this device. The API
// has no per-user preference store, so this lives in localStorage. Storage can be
// missing or throw (private mode, blocked site data), so every access is guarded,
// and a page-lifetime memory keeps the flow from reappearing within the visit.

export const ONBOARDING_STEPS = ["deck", "material", "verify", "goal", "study"] as const;
export type OnboardingStep = (typeof ONBOARDING_STEPS)[number];

export interface OnboardingState {
  status: "active" | "done";
  step: OnboardingStep;
  /** The deck chosen or created in the first step, if any. */
  deckId: string | null;
}

const KEY = "flashcards.onboarding.";
const memory = new Map<string, OnboardingState>();

function isStep(value: unknown): value is OnboardingStep {
  return typeof value === "string" && (ONBOARDING_STEPS as readonly string[]).includes(value);
}

/** Anything stored that does not have the expected shape counts as never started. */
function parse(raw: string | null): OnboardingState | null {
  if (raw === null) {
    return null;
  }
  try {
    const value: unknown = JSON.parse(raw);
    if (typeof value !== "object" || value === null) {
      return null;
    }
    const { status, step, deckId } = value as Record<string, unknown>;
    if ((status !== "active" && status !== "done") || !isStep(step)) {
      return null;
    }
    return { status, step, deckId: typeof deckId === "string" && deckId !== "" ? deckId : null };
  } catch {
    return null;
  }
}

/** null when this user has never started (or finished) the guided setup on this device. */
export function readOnboarding(userId: string): OnboardingState | null {
  const remembered = memory.get(userId);
  if (remembered !== undefined) {
    return remembered;
  }
  try {
    return parse(localStorage.getItem(KEY + userId));
  } catch {
    return null;
  }
}

export function saveOnboarding(userId: string, state: OnboardingState): void {
  memory.set(userId, state);
  try {
    localStorage.setItem(KEY + userId, JSON.stringify(state));
  } catch {
    // Storage unavailable: remembered for this visit only.
  }
}

export function startOnboarding(userId: string): OnboardingState {
  const state: OnboardingState = { status: "active", step: "deck", deckId: null };
  saveOnboarding(userId, state);
  return state;
}

export function finishOnboarding(userId: string): void {
  const current = readOnboarding(userId);
  saveOnboarding(userId, { status: "done", step: current?.step ?? "deck", deckId: current?.deckId ?? null });
}

export function forgetOnboarding(userId: string): void {
  memory.delete(userId);
  try {
    localStorage.removeItem(KEY + userId);
  } catch {
    // Nothing stored.
  }
}

/** Tests only: forget what this page lifetime remembers. */
export function resetOnboardingForTests(): void {
  memory.clear();
}

export function stepNumber(step: OnboardingStep): number {
  return ONBOARDING_STEPS.indexOf(step) + 1;
}

export function nextStep(step: OnboardingStep): OnboardingStep | null {
  return ONBOARDING_STEPS[ONBOARDING_STEPS.indexOf(step) + 1] ?? null;
}
