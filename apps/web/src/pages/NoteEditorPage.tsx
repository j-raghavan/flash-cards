// SPDX-License-Identifier: AGPL-3.0-only
// Create or edit a note (F-CARD-1, F-CARD-2, F-CARD-6): Basic or Cloze, with
// tags and a live preview. Ctrl or Cmd + Enter saves.

import { useId, useRef, useState, type SubmitEvent, type KeyboardEvent, type RefObject } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { describeError } from "../api/client";
import { notes } from "../api/endpoints";
import type { Fields, Note, NoteType } from "../api/types";
import { CardContent, ConfirmButton, ErrorText, Loading } from "../components/ui";
import { cardSides } from "../lib/cardSides";
import { clozeNumbers, nextClozeNumber } from "../lib/cloze";
import { parseTags } from "../lib/text";
import { useResource } from "../lib/useResource";
import { useSubmit } from "../lib/useSubmit";

interface EditorProps {
  deckId: string;
  initial?: Note;
  onSaved: (message: string) => void;
}

function TextArea({
  label,
  value,
  onChange,
  textareaRef,
  hint,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  textareaRef?: RefObject<HTMLTextAreaElement | null>;
  hint?: string;
}) {
  const id = useId();
  const hintId = `${id}-hint`;
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <textarea
        id={id}
        ref={textareaRef}
        rows={4}
        maxLength={20000}
        value={value}
        aria-describedby={hint === undefined ? undefined : hintId}
        onChange={(event) => {
          onChange(event.target.value);
        }}
      />
      {hint === undefined ? null : (
        <small id={hintId} className="muted">
          {hint}
        </small>
      )}
    </div>
  );
}

function NoteEditor({ deckId, initial, onSaved }: EditorProps) {
  const editing = initial !== undefined;
  const [noteType, setNoteType] = useState<NoteType>(initial?.note_type ?? "basic");
  const [front, setFront] = useState(initial?.fields.front ?? "");
  const [back, setBack] = useState(initial?.fields.back ?? "");
  const [text, setText] = useState(initial?.fields.text ?? "");
  const [extra, setExtra] = useState(initial?.fields.extra ?? "");
  const [tags, setTags] = useState(initial?.tags.join(" ") ?? "");
  const clozeRef = useRef<HTMLTextAreaElement>(null);
  const submit = useSubmit();

  const own: Fields = noteType === "basic" ? { front, back } : { text, extra };
  // Fields this editor does not show (from an import, say) are kept as they are.
  const fields: Fields = { ...initial?.fields, ...own };
  const numbers = noteType === "cloze" ? clozeNumbers(text) : [];
  const ready = noteType === "basic" ? front.trim() !== "" && back.trim() !== "" : numbers.length > 0;
  const preview = cardSides(noteType, fields, numbers[0] ?? 1);

  const addDeletion = (): void => {
    const area = clozeRef.current;
    const start = area?.selectionStart ?? text.length;
    const end = area?.selectionEnd ?? text.length;
    const selected = text.slice(start, end) || "answer";
    const wrapped = `{{c${nextClozeNumber(text)}::${selected}}}`;
    setText(`${text.slice(0, start)}${wrapped}${text.slice(end)}`);
    area?.focus();
  };

  const save = (): void => {
    if (!ready || submit.busy) {
      return;
    }
    void submit.run(async () => {
      const tagList = parseTags(tags);
      if (initial === undefined) {
        const made = await notes.create(deckId, noteType, fields, tagList);
        setFront("");
        setBack("");
        setText("");
        setExtra("");
        onSaved(`Added ${made.length} card${made.length === 1 ? "" : "s"}.`);
      } else {
        await notes.update(initial.id, fields, tagList);
        onSaved("Saved.");
      }
    });
  };

  const onSubmit = (event: SubmitEvent): void => {
    event.preventDefault();
    save();
  };

  const onKeyDown = (event: KeyboardEvent): void => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      save();
    }
  };

  return (
    <form onSubmit={onSubmit} onKeyDown={onKeyDown} className="editor">
      <div className="stack">
        <fieldset className="segmented" disabled={editing}>
          <legend>Card type</legend>
          {(["basic", "cloze"] as const).map((type) => (
            <label key={type}>
              <input
                type="radio"
                name="note-type"
                value={type}
                checked={noteType === type}
                onChange={() => {
                  setNoteType(type);
                }}
              />
              {type === "basic" ? "Basic" : "Cloze"}
            </label>
          ))}
        </fieldset>
        {noteType === "basic" ? (
          <>
            <TextArea label="Front" value={front} onChange={setFront} hint="Markdown and $math$ are supported." />
            <TextArea label="Back" value={back} onChange={setBack} />
          </>
        ) : (
          <>
            <TextArea
              label="Text"
              value={text}
              onChange={setText}
              textareaRef={clozeRef}
              hint="Wrap each hidden part as {{c1::answer}}; each number becomes one card."
            />
            <div className="row">
              <button type="button" className="button" onClick={addDeletion}>
                Hide selection as c{nextClozeNumber(text)}
              </button>
              <span className="muted">
                {numbers.length === 0
                  ? "No deletions yet."
                  : `${numbers.length} card${numbers.length === 1 ? "" : "s"} from this note.`}
              </span>
            </div>
            <TextArea label="Extra" value={extra} onChange={setExtra} hint="Shown with the answer. Optional." />
          </>
        )}
        <div className="field">
          <label htmlFor="note-tags">Tags</label>
          <input
            id="note-tags"
            value={tags}
            placeholder="biology chapter::3"
            aria-describedby="tags-hint"
            onChange={(event) => {
              setTags(event.target.value);
            }}
          />
          <small id="tags-hint" className="muted">
            Separate tags with spaces. Use :: for levels.
          </small>
        </div>
        <ErrorText message={submit.error} />
        <div className="row">
          <button type="submit" className="button primary" disabled={!ready || submit.busy}>
            {editing ? "Save changes" : "Add card"}
          </button>
          <span className="muted">
            or <kbd>Ctrl</kbd> + <kbd>Enter</kbd>
          </span>
        </div>
      </div>
      <aside className="preview" aria-label="Preview">
        <h2>Preview</h2>
        <CardContent markdown={preview.front} />
        <hr />
        <CardContent markdown={preview.back} />
      </aside>
    </form>
  );
}

export function NewNotePage() {
  const { deckId = "" } = useParams();
  const [message, setMessage] = useState<string | null>(null);
  return (
    <section>
      <nav aria-label="Breadcrumb" className="breadcrumb">
        <Link to={`/decks/${deckId}`}>Back to deck</Link>
      </nav>
      <h1>Add a card</h1>
      <p role="status" className="muted">
        {message}
      </p>
      <NoteEditor deckId={deckId} onSaved={setMessage} />
    </section>
  );
}

export function EditNotePage() {
  const { noteId = "" } = useParams();
  const navigate = useNavigate();
  const note = useResource(() => notes.get(noteId), noteId);
  const [message, setMessage] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  if (note.error !== null) {
    return <ErrorText message={note.error} />;
  }
  if (note.data === undefined) {
    return <Loading />;
  }
  const loaded = note.data;

  return (
    <section>
      <nav aria-label="Breadcrumb" className="breadcrumb">
        <Link to={`/decks/${loaded.deck_id}`}>Back to deck</Link>
      </nav>
      <h1>Edit card</h1>
      <p role="status" className="muted">
        {message}
      </p>
      {loaded.note_type === "basic" || loaded.note_type === "cloze" ? (
        <NoteEditor deckId={loaded.deck_id} initial={loaded} onSaved={setMessage} />
      ) : (
        <p>
          {loaded.note_type === "custom" ? "Custom" : "Image occlusion"} notes can be studied here; they are edited
          through the API for now.
        </p>
      )}
      <div className="row actions">
        <ConfirmButton
          label="Delete note"
          confirmLabel="Delete for good"
          prompt={`Delete this note, its ${loaded.cards.length} card${loaded.cards.length === 1 ? "" : "s"} and their review history?`}
          onConfirm={async () => {
            setDeleteError(null);
            await notes.remove(loaded.id).then(
              () => navigate(`/decks/${loaded.deck_id}`),
              (failure: unknown) => {
                setDeleteError(describeError(failure));
              },
            );
          }}
        />
      </div>
      <ErrorText message={deleteError} />
    </section>
  );
}
