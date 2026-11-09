// SPDX-License-Identifier: AGPL-3.0-only
// Import an Anki package (F-IMP-1..3) in three steps: choose the file and,
// optionally, which decks and tags to take; preview what the import would do
// (a dry run that writes nothing); then import.

import { useEffect, useRef, useState, type SubmitEvent } from "react";
import { Link } from "react-router";

import {
  importApkg,
  MAX_SELECTION_ITEMS,
  MAX_SELECTION_LENGTH,
  type FullImportReport,
  type ImportOptions,
  type UnsupportedKind,
} from "../api/ankiRoundTrip";
import { ErrorText } from "../components/ui";
import { parseTags } from "../lib/text";
import { useDocumentTitle } from "../lib/useDocumentTitle";
import { useSubmit } from "../lib/useSubmit";

const MAX_BYTES = 40 * 1024 * 1024;

const UNSUPPORTED: Record<UnsupportedKind, string> = {
  unknown_note_type: "Notes whose note type is missing from the package",
  unmappable_note: "Notes that do not fit Basic or Cloze",
  invalid_note: "Notes that are too long or malformed",
  duplicate_note: "Duplicate notes",
  dropped_fields: "Notes with extra fields that are left out",
  extra_cards: "Extra card templates or empty cloze deletions",
  filtered_deck: "Cards in filtered decks, placed in their original deck",
  media: "Media files, which are not imported",
  note_without_cards: "Notes without cards",
  skipped_reviews: "Review history entries that cannot be replayed",
  history_truncated: "Review history too large to import",
};

/** Deck names one per line (they may contain spaces); "::" separates levels. */
export function parseDeckNames(input: string): string[] {
  return [...new Set(input.split("\n").map((line) => line.trim()))].filter((line) => line !== "");
}

function selectionProblem(options: ImportOptions): string | null {
  for (const values of [options.decks, options.tags]) {
    if (values.length > MAX_SELECTION_ITEMS || values.some((v) => v.length > MAX_SELECTION_LENGTH)) {
      return `Choose at most ${String(MAX_SELECTION_ITEMS)} decks and ${String(MAX_SELECTION_ITEMS)} tags, each up to ${String(MAX_SELECTION_LENGTH)} characters.`;
    }
  }
  return null;
}

function Report({ report, preview }: { report: FullImportReport; preview: boolean }) {
  const rows: [string, number | undefined][] = [
    [preview ? "Decks to create" : "Decks created", report.decks_created],
    [preview ? "Notes to import" : "Notes imported", report.notes_imported],
    ["Notes skipped", report.notes_skipped],
    ["Notes not selected", report.notes_not_selected],
    [preview ? "Cards that keep their schedule" : "Cards that kept their schedule", report.cards_scheduled],
    [preview ? "Past reviews to import" : "Past reviews imported", report.reviews_imported],
  ];
  const unsupported = report.unsupported ?? [];
  return (
    <>
      <dl className="report">
        {rows
          .filter((row): row is [string, number] => row[1] !== undefined)
          .map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
      </dl>
      {unsupported.length === 0 ? null : (
        <>
          <h3>Not carried over</h3>
          <ul className="plain-list">
            {unsupported.map((item) => (
              <li key={item.kind}>
                {UNSUPPORTED[item.kind]}: {item.count}
                {item.examples.length > 0 ? <span className="muted"> (for example {item.examples.join(", ")})</span> : null}
              </li>
            ))}
          </ul>
        </>
      )}
    </>
  );
}

type Step =
  | { kind: "choose" }
  | { kind: "preview"; file: File; report: FullImportReport }
  | { kind: "done"; report: FullImportReport };

export function ImportPage() {
  useDocumentTitle("Import from Anki");
  const [file, setFile] = useState<File | null>(null);
  const [deckText, setDeckText] = useState("");
  const [tagText, setTagText] = useState("");
  const [step, setStep] = useState<Step>({ kind: "choose" });
  const submit = useSubmit();
  const headingRef = useRef<HTMLHeadingElement>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const restarted = useRef(false);

  // Each step announces itself: focus moves to its heading, and back to the
  // page title when the wizard starts over.
  useEffect(() => {
    if (step.kind !== "choose") {
      headingRef.current?.focus();
    } else if (restarted.current) {
      restarted.current = false;
      titleRef.current?.focus();
    }
  }, [step]);

  const options = (dryRun: boolean): ImportOptions => ({
    dryRun,
    decks: parseDeckNames(deckText),
    tags: parseTags(tagText),
  });

  const run = (chosenFile: File, dryRun: boolean): void => {
    const chosen = options(dryRun);
    const problem = selectionProblem(chosen);
    if (problem !== null) {
      submit.setError(problem);
      return;
    }
    void submit.run(async () => {
      const report = await importApkg(chosenFile, chosen);
      // A server without dry runs imports at once and says nothing of it.
      setStep(
        dryRun && report.dry_run === true ? { kind: "preview", file: chosenFile, report } : { kind: "done", report },
      );
    });
  };

  const onPreview = (event: SubmitEvent): void => {
    event.preventDefault();
    if (file === null) {
      return;
    }
    if (file.size > MAX_BYTES) {
      submit.setError("That file is larger than 40 MB.");
      return;
    }
    run(file, true);
  };

  const restart = (): void => {
    restarted.current = true;
    setStep({ kind: "choose" });
    setFile(null);
    submit.setError(null);
  };

  if (step.kind === "done") {
    return (
      <section>
        <h1 tabIndex={-1} ref={headingRef}>
          Import finished
        </h1>
        <Report report={step.report} preview={false} />
        <div className="row actions">
          <Link className="button primary" to="/">
            Go to your decks
          </Link>
          <button type="button" className="button" onClick={restart}>
            Import another file
          </button>
        </div>
      </section>
    );
  }

  return (
    <section>
      <h1 tabIndex={-1} ref={titleRef}>
        Import from Anki
      </h1>
      <ol className="steps" aria-label="Steps">
        <li aria-current={step.kind === "choose" ? "step" : undefined}>Choose a file</li>
        <li aria-current={step.kind === "preview" ? "step" : undefined}>Check the preview</li>
        <li>Import</li>
      </ol>
      {step.kind === "choose" ? (
        <form className="stack" onSubmit={onPreview} aria-label="Choose a package">
          <label className="field">
            <span>Anki package (.apkg, up to 40 MB)</span>
            <input
              type="file"
              accept=".apkg,application/octet-stream"
              onChange={(event) => {
                setFile(event.target.files?.[0] ?? null);
                submit.setError(null);
              }}
            />
          </label>
          <p className="muted">
            In Anki, use File, Export, choose "Anki Deck Package" and tick "Support older Anki versions".
          </p>
          <details>
            <summary>Import only some decks or tags</summary>
            <div className="stack">
              <div className="field">
                <label htmlFor="import-decks">Decks, one per line</label>
                <textarea
                  id="import-decks"
                  rows={3}
                  value={deckText}
                  placeholder="Languages::Spanish"
                  aria-describedby="import-decks-hint"
                  onChange={(event) => {
                    setDeckText(event.target.value);
                  }}
                />
                <small id="import-decks-hint" className="muted">
                  Sub-decks come along. Leave empty to import every deck.
                </small>
              </div>
              <label className="field">
                <span>Tags, separated by spaces</span>
                <input
                  value={tagText}
                  placeholder="verbs chapter::3"
                  onChange={(event) => {
                    setTagText(event.target.value);
                  }}
                />
              </label>
            </div>
          </details>
          <ErrorText message={submit.error} />
          <div className="row">
            <button type="submit" className="button primary" disabled={file === null || submit.busy}>
              {submit.busy ? "Reading the package…" : "Preview import"}
            </button>
          </div>
        </form>
      ) : (
        <div className="stack">
          <h2 tabIndex={-1} ref={headingRef}>
            Preview of {step.file.name}
          </h2>
          <p className="muted">Nothing has been imported yet.</p>
          <Report report={step.report} preview={true} />
          <ErrorText message={submit.error} />
          <div className="row">
            <button
              type="button"
              className="button primary"
              disabled={submit.busy}
              onClick={() => {
                run(step.file, false);
              }}
            >
              {submit.busy ? "Importing…" : "Import"}
            </button>
            <button type="button" className="button" disabled={submit.busy} onClick={restart}>
              Start over
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
