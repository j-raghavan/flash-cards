// SPDX-License-Identifier: AGPL-3.0-only
// A generation job: live progress, cancel and retry (F-AI-10), then the draft
// cards to review and approve (F-AI-4, F-AI-5). Drafts are labelled as AI
// output and nothing is scheduled until approved.

import { useCallback, useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";

import { describeError } from "../api/client";
import { ai } from "../api/endpoints";
import { TERMINAL_JOB_STATUSES, type Draft, type Job } from "../api/types";
import { CardContent, ErrorText, Loading } from "../components/ui";
import { cardSides } from "../lib/cardSides";
import { clozeRevealAll } from "../lib/cloze";
import { useJobEvents } from "../lib/useJobEvents";
import { useSubmit } from "../lib/useSubmit";

const STATUS_TEXT: Record<Job["status"], string> = {
  queued: "Waiting to start…",
  running: "Generating cards…",
  succeeded: "Finished.",
  failed: "Generation failed.",
  cancelled: "Cancelled.",
};

function DraftReview({ jobId, deckId }: { jobId: string; deckId: string | null }) {
  const [drafts, setDrafts] = useState<Draft[] | null>(null);
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [loadError, setLoadError] = useState<string | null>(null);
  const [approved, setApproved] = useState<{ count: number; deckId: string | null } | null>(null);
  const submit = useSubmit();

  const load = useCallback(() => {
    ai.drafts(jobId).then(
      (list) => {
        setDrafts(list);
        setChosen(new Set(list.map((d) => d.id)));
      },
      (failure: unknown) => {
        setLoadError(describeError(failure));
      },
    );
  }, [jobId]);

  useEffect(load, [load]);

  if (loadError !== null) {
    return <ErrorText message={loadError} />;
  }
  if (drafts === null) {
    return <Loading label="Loading drafts…" />;
  }

  const target = approved?.deckId ?? deckId;
  const deckLink =
    target === null ? null : (
      <Link className="button" to={`/decks/${target}`}>
        Go to the deck
      </Link>
    );

  if (drafts.length === 0) {
    return (
      <div>
        <p role="status">
          {approved === null
            ? "There are no drafts left to review."
            : `Added ${approved.count} card${approved.count === 1 ? "" : "s"}. All drafts are reviewed.`}
        </p>
        {deckLink}
      </div>
    );
  }

  const toggle = (id: string): void => {
    const next = new Set(chosen);
    if (next.has(id)) {
      next.delete(id);
    } else {
      next.add(id);
    }
    setChosen(next);
  };

  const approve = (): void => {
    void submit.run(async () => {
      const made = await ai.approve(jobId, [...chosen]);
      setApproved({ count: made.length, deckId: made[0]?.deck_id ?? null });
      load();
    });
  };

  return (
    <div>
      <h2>Review the drafts</h2>
      <p className="muted">
        These cards were written by AI. Check each one against your source; untick any you do not want, then approve the
        rest.
      </p>
      {approved === null ? null : (
        <p role="status">
          Added {approved.count} card{approved.count === 1 ? "" : "s"}. {deckLink}
        </p>
      )}
      <div className="row">
        <button
          type="button"
          className="link-button"
          onClick={() => {
            setChosen(chosen.size === drafts.length ? new Set() : new Set(drafts.map((d) => d.id)));
          }}
        >
          {chosen.size === drafts.length ? "Untick all" : "Tick all"}
        </button>
      </div>
      <ul className="drafts">
        {drafts.map((draft) => {
          const sides = cardSides(draft.note_type, draft.fields, 1);
          return (
            <li key={draft.id} className={chosen.has(draft.id) ? "draft chosen" : "draft"}>
              <label className="draft-pick">
                <input
                  type="checkbox"
                  checked={chosen.has(draft.id)}
                  onChange={() => {
                    toggle(draft.id);
                  }}
                />
                <span className="muted">
                  AI draft, source page {draft.source_page}
                  {draft.note_type === "cloze" ? ", cloze" : ""}
                </span>
              </label>
              <CardContent
                markdown={draft.note_type === "cloze" ? clozeRevealAll(draft.fields.text ?? "") : sides.front}
              />
              {draft.note_type === "basic" ? <CardContent markdown={sides.back} className="back" /> : null}
            </li>
          );
        })}
      </ul>
      <ErrorText message={submit.error} />
      <button type="button" className="button primary" disabled={chosen.size === 0 || submit.busy} onClick={approve}>
        Approve {chosen.size} card{chosen.size === 1 ? "" : "s"}
      </button>
    </div>
  );
}

export function JobPage() {
  const { jobId = "" } = useParams();
  const [params] = useSearchParams();
  const deckId = params.get("deck");
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const actions = useSubmit();

  const fetchJob = useCallback(() => {
    ai.job(jobId).then(setJob, (failure: unknown) => {
      setError(describeError(failure));
    });
  }, [jobId]);

  useEffect(fetchJob, [fetchJob]);

  const finished = job !== null && TERMINAL_JOB_STATUSES.includes(job.status);
  useJobEvents(jobId, job !== null && !finished, setJob, fetchJob);

  const back =
    deckId === null ? (
      <Link to="/">Back to decks</Link>
    ) : (
      <Link to={`/decks/${deckId}/generate`}>Back to generation</Link>
    );

  if (error !== null) {
    return (
      <section>
        <nav className="breadcrumb">{back}</nav>
        <ErrorText message={error} />
      </section>
    );
  }
  if (job === null) {
    return <Loading />;
  }

  const percent = Math.round(job.progress * 100);

  return (
    <section>
      <nav aria-label="Breadcrumb" className="breadcrumb">
        {back}
      </nav>
      <h1>Card generation</h1>
      <p role="status" aria-live="polite">
        {job.cancel_requested && !finished ? "Cancelling…" : STATUS_TEXT[job.status]}
        {job.total_units > 0 && !finished ? ` ${job.completed_units} of ${job.total_units} pages.` : ""}
      </p>
      {finished ? null : (
        <progress max={100} value={percent} aria-label="Generation progress">
          {percent}%
        </progress>
      )}
      {job.status === "failed" && job.error !== null ? <ErrorText message={job.error} /> : null}
      <ErrorText message={actions.error} />
      <div className="row actions">
        {finished ? null : (
          <button
            type="button"
            className="button"
            disabled={actions.busy || job.cancel_requested}
            onClick={() => {
              void actions.run(async () => {
                setJob(await ai.cancel(jobId));
              });
            }}
          >
            Cancel
          </button>
        )}
        {job.status === "failed" || job.status === "cancelled" ? (
          <button
            type="button"
            className="button primary"
            disabled={actions.busy}
            onClick={() => {
              void actions.run(async () => {
                setJob(await ai.retry(jobId));
              });
            }}
          >
            Try again
          </button>
        ) : null}
      </div>
      {finished ? null : (
        <p className="muted">You can leave this page; generation keeps going and you can come back to it.</p>
      )}
      {job.status === "succeeded" ? <DraftReview jobId={jobId} deckId={deckId} /> : null}
    </section>
  );
}
