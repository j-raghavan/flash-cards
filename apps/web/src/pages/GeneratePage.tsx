// SPDX-License-Identifier: AGPL-3.0-only
// Start AI generation from a PDF or image (F-AI-1, F-AI-3), a YouTube link or a
// pasted transcript (F-AI-2). Generation runs in the background; the job page
// shows progress and the drafts to review.

import { useRef, useState, type SubmitEvent } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { AiNotice } from "../account/AiNotice";
import { ai } from "../api/endpoints";
import type { JobStatus, Thoroughness } from "../api/types";
import { ApiError } from "../api/client";
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

/** Most characters of a pasted transcript; the server enforces the same limit. */
const MAX_TRANSCRIPT_CHARS = 120_000;

function VideoSource({ deckId, level }: { deckId: string; level: Thoroughness }) {
  const navigate = useNavigate();
  const [url, setUrl] = useState("");
  const [transcript, setTranscript] = useState("");
  const link = useSubmit();
  const paste = useSubmit();
  const pasteRef = useRef<HTMLTextAreaElement>(null);
  // Never cut a paste short silently: say it is too long and refuse to send it.
  const tooLong = transcript.length > MAX_TRANSCRIPT_CHARS;
  const open = (jobId: string): void => {
    void navigate(`/jobs/${encodeURIComponent(jobId)}?deck=${encodeURIComponent(deckId)}`);
  };

  const fromLink = (event: SubmitEvent): void => {
    event.preventDefault();
    void link.run(async () => {
      try {
        open((await ai.generateFromYoutube(deckId, url.trim(), level)).id);
      } catch (failure) {
        if (failure instanceof ApiError && failure.status === 422 && /^No transcript/.test(failure.message)) {
          // No transcript to fetch: the paste box is next. Other 422s (not a video
          // link, a transcript too long to use) are not fixed by pasting.
          pasteRef.current?.focus();
        }
        throw failure;
      }
    });
  };
  const fromText = (event: SubmitEvent): void => {
    event.preventDefault();
    void paste.run(async () => {
      open((await ai.generateFromTranscript(deckId, transcript, level)).id);
    });
  };

  return (
    <>
      <h2>From a video or a transcript</h2>
      <form onSubmit={fromLink} className="inline-form" aria-label="Generate from a YouTube link">
        <label className="field grow">
          <span>YouTube link</span>
          <input
            type="url"
            inputMode="url"
            maxLength={2000}
            value={url}
            placeholder="https://www.youtube.com/watch?v=…"
            onChange={(event) => {
              setUrl(event.target.value);
            }}
          />
        </label>
        <button type="submit" className="button" disabled={url.trim() === "" || link.busy}>
          {link.busy ? "Fetching…" : "Use the video's transcript"}
        </button>
      </form>
      <ErrorText message={link.error} />
      <form onSubmit={fromText} className="stack" aria-label="Generate from a pasted transcript">
        <label className="field">
          <span>Or paste a transcript or lecture notes</span>
          <textarea
            ref={pasteRef}
            rows={6}
            value={transcript}
            aria-describedby="transcript-length"
            onChange={(event) => {
              setTranscript(event.target.value);
            }}
          />
        </label>
        <small id="transcript-length" className={tooLong ? "error" : "muted"}>
          {tooLong
            ? `This is ${transcript.length.toLocaleString()} characters; the limit is ${MAX_TRANSCRIPT_CHARS.toLocaleString()}. Split it and generate from each part.`
            : `Up to ${MAX_TRANSCRIPT_CHARS.toLocaleString()} characters.`}
        </small>
        <ErrorText message={paste.error} />
        <div>
          <button type="submit" className="button" disabled={transcript.trim() === "" || tooLong || paste.busy}>
            {paste.busy ? "Sending…" : "Generate from this text"}
          </button>
        </div>
      </form>
    </>
  );
}

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
      <AiNotice />
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
      <VideoSource deckId={deckId} level={level} />
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
