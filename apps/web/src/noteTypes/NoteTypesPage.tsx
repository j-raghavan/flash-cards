// SPDX-License-Identifier: AGPL-3.0-only
// Custom note types (F-CARD-4, ADR 0013): named fields and card templates with
// {{Field}} placeholders, each template making one card per note. Renaming a
// field carries its values along; keeping a template keeps its cards.
// Loaded on first visit.

import { useState, type SubmitEvent } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { describeError } from "../api/client";
import { noteTypes } from "../api/endpoints";
import type { NoteTypeDef, NoteTypeInput } from "../api/types";
import { CardContent, ConfirmButton, ErrorText, Loading } from "../components/ui";
import { cardSides } from "../lib/cardSides";
import { useDocumentTitle } from "../lib/useDocumentTitle";
import { useResource } from "../lib/useResource";
import { useSubmit } from "../lib/useSubmit";

interface FieldRow {
  key: number;
  /** The field's saved name, or null for a field added here. */
  original: string | null;
  name: string;
}

interface TemplateRow {
  key: number;
  ordinal?: number;
  name: string;
  front: string;
  back: string;
}

let nextKey = 1;
const key = (): number => nextKey++;

// The server's limits (domain/note_types.py, domain/notes.py).
export const MAX_NAME = 100;
export const MAX_FIELD_NAME = 64;
export const MAX_FIELDS = 64;
export const MAX_TEMPLATE_NAME = 64;
export const MAX_TEMPLATES = 16;

/** Saved fields given a new name, as old name to new name. */
function renames(fields: FieldRow[]): Record<string, string> {
  const renamed: Record<string, string> = {};
  for (const field of fields) {
    if (field.original !== null && field.original !== field.name.trim()) {
      renamed[field.original] = field.name.trim();
    }
  }
  return renamed;
}

/** A template with each renamed field's {{placeholder}} following the new name. */
function renamePlaceholders(template: string, renamed: Record<string, string>): string {
  return template.replace(/\{\{(.*?)\}\}/gs, (match, field: string) => {
    const next = renamed[field];
    return next === undefined ? match : `{{${next}}}`;
  });
}

/** The update request: names, templates, and which saved fields were renamed. */
export function toInput(
  name: string,
  fields: FieldRow[],
  templates: TemplateRow[],
): NoteTypeInput & { renamed: Record<string, string> } {
  const renamed = renames(fields);
  return {
    name: name.trim(),
    fields: fields.map((f) => f.name.trim()),
    templates: templates.map(({ ordinal, name: templateName, front, back }) => {
      const sides = {
        name: templateName,
        front: renamePlaceholders(front, renamed),
        back: renamePlaceholders(back, renamed),
      };
      return ordinal === undefined ? sides : { ordinal, ...sides };
    }),
    renamed,
  };
}

export function NoteTypesPage() {
  useDocumentTitle("Note types");
  const list = useResource(() => noteTypes.list(), "note-types");
  return (
    <section>
      <h1>Note types</h1>
      <p className="muted">
        A note type names its fields and how each card shows them. Use it when Basic and Cloze do not fit, such as a
        word with its meaning, pronunciation and example.
      </p>
      <ErrorText message={list.error} />
      {list.data === undefined ? (
        list.error === null ? (
          <Loading />
        ) : null
      ) : list.data.length === 0 ? (
        <p className="empty">No note types yet.</p>
      ) : (
        <ul className="deck-tree">
          {list.data.map((type) => (
            <li key={type.id}>
              <Link to={`/note-types/${type.id}`}>{type.name}</Link>{" "}
              <span className="muted">
                {type.fields.length} field{type.fields.length === 1 ? "" : "s"}, {type.templates.length} card
                {type.templates.length === 1 ? "" : "s"} per note
              </span>
            </li>
          ))}
        </ul>
      )}
      <p>
        <Link className="button primary" to="/note-types/new">
          New note type
        </Link>
      </p>
    </section>
  );
}

function NoteTypeForm({ initial, onSaved }: { initial?: NoteTypeDef; onSaved: (saved: NoteTypeDef) => void }) {
  const [name, setName] = useState(initial?.name ?? "");
  const [fields, setFields] = useState<FieldRow[]>(
    initial?.fields.map((f) => ({ key: key(), original: f, name: f })) ?? [
      { key: key(), original: null, name: "Front" },
      { key: key(), original: null, name: "Back" },
    ],
  );
  const [templates, setTemplates] = useState<TemplateRow[]>(
    initial?.templates.map((t) => ({ key: key(), ...t })) ?? [
      { key: key(), name: "Card 1", front: "{{Front}}", back: "{{FrontSide}}\n\n---\n\n{{Back}}" },
    ],
  );
  const submit = useSubmit();
  const names = fields.map((f) => f.name.trim());
  const duplicate = names.some((n, i) => n !== "" && names.indexOf(n) !== i);
  const ready =
    name.trim() !== "" &&
    fields.length > 0 &&
    names.every((n) => n !== "") &&
    !duplicate &&
    templates.length > 0 &&
    templates.every((t) => t.name.trim() !== "" && t.front.trim() !== "");
  const sample = Object.fromEntries(names.map((n) => [n, `*${n}*`]));
  // Never empty: the last template cannot be removed.
  const first = templates[0] as TemplateRow;
  const renamed = renames(fields);
  const preview = cardSides("custom", sample, 0, {
    ordinal: 0,
    name: first.name,
    front: renamePlaceholders(first.front, renamed),
    back: renamePlaceholders(first.back, renamed),
  });

  const setField = (index: number, value: string): void => {
    setFields(fields.map((f, i) => (i === index ? { ...f, name: value } : f)));
  };
  const setTemplate = (index: number, changes: Partial<TemplateRow>): void => {
    setTemplates(templates.map((t, i) => (i === index ? { ...t, ...changes } : t)));
  };

  const onSubmit = (event: SubmitEvent): void => {
    event.preventDefault();
    void submit.run(async () => {
      const input = toInput(name, fields, templates);
      if (initial === undefined) {
        onSaved(await noteTypes.create({ name: input.name, fields: input.fields, templates: input.templates }));
      } else {
        onSaved(await noteTypes.update(initial.id, input));
      }
    });
  };

  return (
    <form className="editor" onSubmit={onSubmit} aria-label="Note type">
      <div className="stack">
        <label className="field">
          <span>Name</span>
          <input
            required
            maxLength={MAX_NAME}
            value={name}
            onChange={(event) => {
              setName(event.target.value);
            }}
          />
        </label>
        <fieldset>
          <legend>Fields</legend>
          <ol className="stack plain-list">
            {fields.map((field, index) => (
              <li key={field.key} className="row">
                <label className="field grow">
                  <span>Field {index + 1}</span>
                  <input
                    value={field.name}
                    maxLength={MAX_FIELD_NAME}
                    onChange={(event) => {
                      setField(index, event.target.value);
                    }}
                  />
                </label>
                <button
                  type="button"
                  className="button danger-text"
                  disabled={fields.length === 1}
                  aria-label={`Remove field ${String(index + 1)}`}
                  onClick={() => {
                    setFields(fields.filter((_f, i) => i !== index));
                  }}
                >
                  Remove
                </button>
              </li>
            ))}
          </ol>
          {duplicate ? <p className="error">Each field needs its own name.</p> : null}
          <button
            type="button"
            className="button"
            disabled={fields.length >= MAX_FIELDS}
            onClick={() => {
              setFields([...fields, { key: key(), original: null, name: "" }]);
            }}
          >
            Add a field
          </button>
          {initial === undefined ? null : (
            <p className="muted">
              Renaming a field keeps its contents and updates the card templates. Removing one deletes its contents from
              every note.
            </p>
          )}
        </fieldset>
        <fieldset>
          <legend>Cards</legend>
          <p className="muted">
            Each card template makes one card per note. Write {"{{Field}}"} where a field goes; on the back,{" "}
            {"{{FrontSide}}"} repeats the front.
          </p>
          {templates.map((template, index) => (
            <fieldset key={template.key} className="panel stack">
              <legend>Card {index + 1}</legend>
              <label className="field">
                <span>Card name</span>
                <input
                  value={template.name}
                  maxLength={MAX_TEMPLATE_NAME}
                  onChange={(event) => {
                    setTemplate(index, { name: event.target.value });
                  }}
                />
              </label>
              <label className="field">
                <span>Front template</span>
                <textarea
                  rows={3}
                  value={template.front}
                  onChange={(event) => {
                    setTemplate(index, { front: event.target.value });
                  }}
                />
              </label>
              <label className="field">
                <span>Back template</span>
                <textarea
                  rows={3}
                  value={template.back}
                  onChange={(event) => {
                    setTemplate(index, { back: event.target.value });
                  }}
                />
              </label>
              <div className="row">
                <button
                  type="button"
                  className="button danger-text"
                  disabled={templates.length === 1}
                  onClick={() => {
                    setTemplates(templates.filter((_t, i) => i !== index));
                  }}
                >
                  Remove card {index + 1}
                </button>
              </div>
            </fieldset>
          ))}
          <button
            type="button"
            className="button"
            disabled={templates.length >= MAX_TEMPLATES}
            onClick={() => {
              setTemplates([
                ...templates,
                { key: key(), name: `Card ${String(templates.length + 1)}`, front: "", back: "{{FrontSide}}" },
              ]);
            }}
          >
            Add a card
          </button>
        </fieldset>
        <ErrorText message={submit.error} />
        <div className="row">
          <button type="submit" className="button primary" disabled={!ready || submit.busy}>
            {initial === undefined ? "Create note type" : "Save note type"}
          </button>
        </div>
      </div>
      <aside className="preview" aria-label="Preview">
        <h2>Preview of card 1</h2>
        <CardContent markdown={preview.front} />
        <hr />
        <CardContent markdown={preview.back} />
      </aside>
    </form>
  );
}

export function NewNoteTypePage() {
  useDocumentTitle("New note type");
  const navigate = useNavigate();
  return (
    <section>
      <nav aria-label="Breadcrumb" className="breadcrumb">
        <Link to="/note-types">Note types</Link>
      </nav>
      <h1>New note type</h1>
      <NoteTypeForm
        onSaved={(saved) => {
          void navigate(`/note-types/${saved.id}`);
        }}
      />
    </section>
  );
}

export function EditNoteTypePage() {
  const { noteTypeId = "" } = useParams();
  const navigate = useNavigate();
  const type = useResource(() => noteTypes.get(noteTypeId), `note-type:${noteTypeId}`);
  const [message, setMessage] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  useDocumentTitle(type.data?.name ?? "Note type");

  if (type.error !== null) {
    return (
      <section>
        <nav aria-label="Breadcrumb" className="breadcrumb">
          <Link to="/note-types">Note types</Link>
        </nav>
        <ErrorText message={type.error} />
      </section>
    );
  }
  if (type.data === undefined) {
    return <Loading />;
  }
  const current = type.data;

  return (
    <section>
      <nav aria-label="Breadcrumb" className="breadcrumb">
        <Link to="/note-types">Note types</Link>
      </nav>
      <h1>{current.name}</h1>
      <p role="status" className="muted">
        {message}
      </p>
      <NoteTypeForm
        key={version}
        initial={current}
        onSaved={(saved) => {
          type.setData(saved);
          // Start the form again from what was saved, so later renames compare with it.
          setVersion((n) => n + 1);
          setMessage("Saved.");
        }}
      />
      <div className="row actions">
        <ConfirmButton
          label="Delete note type"
          confirmLabel="Delete note type"
          prompt={`Delete the note type “${current.name}”? A note type that notes still use cannot be deleted.`}
          onConfirm={async () => {
            setDeleteError(null);
            try {
              await noteTypes.remove(current.id);
              void navigate("/note-types");
            } catch (failure) {
              setDeleteError(describeError(failure));
            }
          }}
        />
      </div>
      <ErrorText message={deleteError} />
    </section>
  );
}
