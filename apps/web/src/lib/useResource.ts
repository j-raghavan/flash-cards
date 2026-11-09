// SPDX-License-Identifier: AGPL-3.0-only
import { useCallback, useEffect, useRef, useState } from "react";

import { describeError } from "../api/client";

export interface Resource<T> {
  data: T | undefined;
  error: string | null;
  loading: boolean;
  reload: () => void;
  setData: (data: T) => void;
}

interface Loaded<T> {
  key: string;
  request: string;
  data: T | undefined;
  error: string | null;
}

/**
 * Load something when `key` changes; answers for an older key are ignored.
 * A reload keeps the current data visible until the new answer arrives.
 */
export function useResource<T>(load: () => Promise<T>, key: string): Resource<T> {
  const [generation, setGeneration] = useState(0);
  const [loaded, setLoaded] = useState<Loaded<T>>({ key: "", request: "", data: undefined, error: null });
  const loadRef = useRef(load);
  useEffect(() => {
    loadRef.current = load;
  });

  const request = `${String(generation)}:${key}`;

  useEffect(() => {
    let current = true;
    loadRef.current().then(
      (data) => {
        if (current) {
          setLoaded({ key, request, data, error: null });
        }
      },
      (failure: unknown) => {
        if (current) {
          setLoaded((previous) => ({
            key,
            request,
            data: previous.key === key ? previous.data : undefined,
            error: describeError(failure),
          }));
        }
      },
    );
    return () => {
      current = false;
    };
  }, [key, request]);

  const reload = useCallback(() => {
    setGeneration((n) => n + 1);
  }, []);

  const setData = useCallback((data: T) => {
    setLoaded((previous) => ({ ...previous, data }));
  }, []);

  const loading = loaded.request !== request;
  return {
    // Data for another key (another deck, say) is never shown for this one.
    data: loaded.key === key ? loaded.data : undefined,
    error: loading ? null : loaded.error,
    loading,
    reload,
    setData,
  };
}
