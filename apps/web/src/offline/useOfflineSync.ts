// SPDX-License-Identifier: AGPL-3.0-only
import { useCallback, useEffect, useState } from "react";

import { flush, pending, subscribe } from "./reviewQueue";

export interface OfflineSync {
  online: boolean;
  /** Answers waiting to be sent. */
  waiting: number;
  syncNow: () => Promise<void>;
}

async function countAfterSync(userId: string): Promise<number> {
  if (navigator.onLine) {
    await flush(userId);
  }
  return (await pending(userId)).length;
}

/** Tracks connectivity and replays queued answers when the connection returns. */
export function useOfflineSync(userId: string | null): OfflineSync {
  const [online, setOnline] = useState(() => navigator.onLine);
  const [waiting, setWaiting] = useState(0);

  const syncNow = useCallback(async () => {
    if (userId !== null) {
      setWaiting(await countAfterSync(userId));
    }
  }, [userId]);

  useEffect(() => {
    if (userId === null) {
      return;
    }
    let active = true;
    const update = (count: Promise<number>): void => {
      void count.then((n) => {
        if (active) {
          setWaiting(n);
        }
      });
    };
    const goOnline = (): void => {
      setOnline(true);
      update(countAfterSync(userId));
    };
    const goOffline = (): void => {
      setOnline(false);
    };
    window.addEventListener("online", goOnline);
    window.addEventListener("offline", goOffline);
    const unsubscribe = subscribe(() => {
      update(pending(userId).then((list) => list.length));
    });
    update(countAfterSync(userId));
    return () => {
      active = false;
      window.removeEventListener("online", goOnline);
      window.removeEventListener("offline", goOffline);
      unsubscribe();
    };
  }, [userId]);

  return { online, waiting: userId === null ? 0 : waiting, syncNow };
}
