// SPDX-License-Identifier: AGPL-3.0-only
// The API has no "list my jobs" route, so the device remembers the last few
// generation jobs per deck, letting the user leave and come back to them.

const KEY = "flashcards.recentJobs";
const PER_DECK = 5;

export interface RecentJob {
  jobId: string;
  deckId: string;
  fileName: string;
  startedAt: string;
}

function readAll(): RecentJob[] {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(KEY) ?? "[]");
    return Array.isArray(parsed) ? (parsed as RecentJob[]) : [];
  } catch {
    return [];
  }
}

export function rememberJob(job: RecentJob): void {
  const others = readAll().filter((j) => j.jobId !== job.jobId);
  const sameDeck = others.filter((j) => j.deckId === job.deckId).slice(0, PER_DECK - 1);
  const rest = others.filter((j) => j.deckId !== job.deckId);
  try {
    localStorage.setItem(KEY, JSON.stringify([job, ...sameDeck, ...rest]));
  } catch {
    // Without storage the job is still reachable from its own URL.
  }
}

export function recentJobs(deckId: string): RecentJob[] {
  return readAll().filter((j) => j.deckId === deckId);
}

export function forgetAllJobs(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    // Nothing stored.
  }
}
