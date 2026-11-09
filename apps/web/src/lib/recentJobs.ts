// SPDX-License-Identifier: AGPL-3.0-only
// Earlier builds remembered recent generation jobs (with file names) in
// localStorage. The list now comes from GET /ai/jobs; this removes what an
// earlier build may have left behind, so it does not outlive a sign-out.

const LEGACY_KEY = "flashcards.recentJobs";

export function forgetAllJobs(): void {
  try {
    localStorage.removeItem(LEGACY_KEY);
  } catch {
    // Nothing stored.
  }
}
