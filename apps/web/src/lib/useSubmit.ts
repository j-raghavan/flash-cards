// SPDX-License-Identifier: AGPL-3.0-only
import { useCallback, useState } from "react";

import { describeError } from "../api/client";

export interface Submit {
  busy: boolean;
  error: string | null;
  setError: (message: string | null) => void;
  run: (action: () => Promise<void>) => Promise<void>;
}

/** Busy and error state around one user action. */
export function useSubmit(): Submit {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = useCallback(async (action: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (failure) {
      setError(describeError(failure));
    } finally {
      setBusy(false);
    }
  }, []);
  return { busy, error, setError, run };
}
