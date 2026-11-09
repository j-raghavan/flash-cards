// SPDX-License-Identifier: AGPL-3.0-only

/** Must match the runtime cache name in vite.config.ts. */
export const STUDY_DATA_CACHE = "flashcards-study-data";

/** Remove cached study data, so the next person on this device cannot read it. */
export async function clearStudyDataCache(): Promise<void> {
  if (typeof caches === "undefined") {
    return;
  }
  try {
    await caches.delete(STUDY_DATA_CACHE);
  } catch {
    // A browser that blocks Cache Storage has nothing cached either.
  }
}
