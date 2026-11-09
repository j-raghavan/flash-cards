// SPDX-License-Identifier: AGPL-3.0-only
// Start AI generation from a PDF or image (F-AI-1, F-AI-3). Generation runs in
// the background; the job page shows progress and the drafts to review.

import { useState, type SubmitEvent } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { ai } from "../api/endpoints";
import type { JobStatus, Thoroughness } from "../api/types";
import { ErrorText } from "../components/ui";
import { useResource } from "../lib/useResource";
import { useSubmit } from "../lib/useSubmit";
import { useDocumentTitle } from "../lib/useDocumentTitle";

const STATUS_LABELS: Record<JobStatus, string> = {
  queued: "Waiting to start",
  running: "In progress",
  succeeded: "Finished",
  failed: "Failed",
  cancelled: "Cancelled",
};

const MAX_BYTES = 25 * 1024 * 1024;
const ACCEPTED = ["application/pdf", "image/png", "image/jpeg"];

const LEVELS: { value: Thoroughness; label: string; hint: string }[] = [
  { value: "concise", label: "Concise", hint: "Key ideas only." },
  { value: "standard", label: "Standard", hint: "A balanced set." },
  { value: "thorough", label: "Thorough", hint: "Detailed coverage." },
];

export function GeneratePage() {
  useDocumentTitle("Generate cards");
  const { deckId = "" } = useParams();
  const navigate = useNavigate();
  const [file, setFile] = useState<File | null>(null);
  const [level, setLevel] = useState<Thoroughness>("standard");
  const submit = useSubmit();
  // Recent jobs come from the server, so they follow the user across devices.
  // The list is a convenience: if it cannot load, the page works without it.
  const recent = useResource(() => ai.jobs(deckId), `jobs:${deckId}`).data ?? [];

  const onSubmit = (event: SubmitEvent): void => {
    event.preventDefault();
    if (file === null) {
      return;
    }
    if (!ACCEPTED.includes(file.type)) {
      submit.setError("Choose a PDF, PNG or JPEG file.");
      return;
    }
    if (file.size > MAX_BYTES) {
      submit.setError("That file is larger than 25 MB.");
      return;
    }
    void submit.run(async () => {
      const job = await ai.generate(deckId, file, level);
      void navigate(`/jobs/${job.id}?deck=${encodeURIComponent(deckId)}`);
    });
  };

  return (
    <section>
      <nav aria-label="Breadcrumb" className="breadcrumb">
        <Link to={`/decks/${deckId}`}>Back to deck</Link>
      </nav>
      <h1>Generate cards from a file</h1>
      <p className="muted">
        Upload lecture slides, a PDF or a photo of your notes. The AI drafts cards; nothing is added to the deck until
        you approve it.
      </p>
      <form onSubmit={onSubmit} className="stack">
        <label className="field">
          <span>File (PDF, PNG or JPEG, up to 25 MB)</span>
          <input
            type="file"
            accept={ACCEPTED.join(",")}
            onChange={(event) => {
              setFile(event.target.files?.[0] ?? null);
              submit.setError(null);
            }}
          />
        </label>
        <fieldset className="segmented">
          <legend>Thoroughness</legend>
          {LEVELS.map((option) => (
            <label key={option.value} title={option.hint}>
              <input
                type="radio"
                name="thoroughness"
                value={option.value}
                checked={level === option.value}
                onChange={() => {
                  setLevel(option.value);
                }}
              />
              {option.label}
            </label>
          ))}
        </fieldset>
        <ErrorText message={submit.error} />
        <div>
          <button type="submit" className="button primary" disabled={file === null || submit.busy}>
            {submit.busy ? "Uploading…" : "Generate drafts"}
          </button>
        </div>
      </form>
      {recent.length > 0 ? (
        <>
          <h2>Recent generations</h2>
          <ul className="plain-list">
            {recent.map((job) => (
              <li key={job.id}>
                <Link to={`/jobs/${encodeURIComponent(job.id)}?deck=${encodeURIComponent(deckId)}`}>
                  {new Date(job.created_at).toLocaleString()}
                </Link>{" "}
                <span className="muted">{STATUS_LABELS[job.status]}</span>
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </section>
  );
}
