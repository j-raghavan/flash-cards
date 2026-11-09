// SPDX-License-Identifier: AGPL-3.0-only
// Live job progress over Server-Sent Events (F-AI-10, ADR 0012). The server
// sends `progress` snapshots and one `done`; it answers 204 for a job that has
// already finished, which EventSource reports as an error with the stream
// closed. Either way the client then reads the job once more.

import { useEffect, useRef } from "react";

import { apiUrl } from "../api/client";
import { ai } from "../api/endpoints";
import type { Job } from "../api/types";

function parseJob(event: MessageEvent<unknown>): Job | null {
  if (typeof event.data !== "string") {
    return null;
  }
  try {
    return JSON.parse(event.data) as Job;
  } catch {
    return null;
  }
}

/** While `active`, push every job snapshot to `onJob`; `onClosed` if the stream ends without `done`. */
export function useJobEvents(
  jobId: string,
  attempt: number,
  active: boolean,
  onJob: (job: Job) => void,
  onClosed: () => void,
): void {
  const handlers = useRef({ onJob, onClosed });
  useEffect(() => {
    handlers.current = { onJob, onClosed };
  });

  useEffect(() => {
    if (!active || typeof EventSource === "undefined") {
      return;
    }
    const source = new EventSource(apiUrl(ai.eventsPath(jobId)));
    const onProgress = (event: MessageEvent<unknown>): void => {
      const job = parseJob(event);
      if (job !== null) {
        handlers.current.onJob(job);
      }
    };
    const onDone = (event: MessageEvent<unknown>): void => {
      source.close(); // the snapshot it carries is final
      onProgress(event);
    };
    source.addEventListener("progress", onProgress);
    source.addEventListener("done", onDone);
    source.onerror = () => {
      // CONNECTING means the browser will retry on its own; CLOSED means it gave up
      // (a 204 for a finished job, a 404 or a 429).
      if (source.readyState === EventSource.CLOSED) {
        handlers.current.onClosed();
      }
    };
    return () => {
      source.close();
    };
  }, [jobId, attempt, active]);
}
