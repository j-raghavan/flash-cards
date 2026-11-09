// SPDX-License-Identifier: AGPL-3.0-only
// One deck: its statistics and proficiency (F-PROG-2, F-PROG-4), sub-decks, and
// the ways in to study, add, browse and generate cards.

import { lazy, Suspense, useState, type SubmitEvent } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { describeError } from "../api/client";
import { exportDeck, saveFile, type ExportFormat } from "../api/ankiRoundTrip";
import { decks } from "../api/endpoints";
import type { Deck, DeckStats } from "../api/types";
import { ConfirmButton, ErrorText, Loading, ProficiencyRing } from "../components/ui";
import { buildDeckTree, deckPath, descendantCount, flattenTree } from "../lib/deckTree";
import { useResource } from "../lib/useResource";
import { useSubmit } from "../lib/useSubmit";
import { CreateDeckForm } from "./DeckListPage";
import { useDocumentTitle } from "../lib/useDocumentTitle";

// The AI panels load after the page, so they add nothing to the start bundle.
const DeckSummaryPanel = lazy(() =>
  import("../aiStudy/DeckSummaryPanel").then((m) => ({ default: m.DeckSummaryPanel })),
);
const DeckChatPanel = lazy(() => import("../aiStudy/DeckChatPanel").then((m) => ({ default: m.DeckChatPanel })));

function StatsPanel({ stats }: { stats: DeckStats }) {
  const rows: [string, number][] = [
    ["Due now", stats.due],
    ["New", stats.new],
    ["Learning", stats.learning],
    ["Review", stats.review],
    ["Mature", stats.mature],
    ["Suspended", stats.suspended],
    ["Total", stats.total],
  ];
  return (
    <div className="stats">
      <ProficiencyRing value={stats.proficiency} />
      <dl>
        {rows.map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

const EXPORTS: { format: ExportFormat; label: string }[] = [
  { format: "apkg", label: "Anki package" },
  { format: "csv", label: "CSV" },
  { format: "json", label: "JSON" },
];

/** Download the deck with its sub-decks (F-IMP-4). */
function ExportDeck({ deckId }: { deckId: string }) {
  const submit = useSubmit();
  const [note, setNote] = useState<string | null>(null);
  return (
    <div className="stack">
      <div className="row" role="group" aria-label="Export this deck">
        <span className="muted">Export with sub-decks:</span>
        {EXPORTS.map(({ format, label }) => (
          <button
            key={format}
            type="button"
            className="button"
            disabled={submit.busy}
            onClick={() => {
              setNote(null);
              void submit.run(async () => {
                const exported = await exportDeck(deckId, format);
                saveFile(exported.blob, exported.filename);
                setNote(
                  exported.omittedNotes > 0
                    ? `Downloaded. ${String(exported.omittedNotes)} custom or image occlusion note${exported.omittedNotes === 1 ? " was" : "s were"} left out; JSON keeps them.`
                    : "Downloaded.",
                );
              });
            }}
          >
            {label}
          </button>
        ))}
      </div>
      <p role="status" className="muted">
        {note}
      </p>
      <ErrorText message={submit.error} />
    </div>
  );
}

function RenameForm({ deck, onRenamed }: { deck: Deck; onRenamed: (deck: Deck) => void }) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(deck.name);
  const submit = useSubmit();

  if (!editing) {
    return (
      <button
        type="button"
        className="button"
        onClick={() => {
          setName(deck.name);
          setEditing(true);
        }}
      >
        Rename
      </button>
    );
  }

  const onSubmit = (event: SubmitEvent): void => {
    event.preventDefault();
    void submit.run(async () => {
      onRenamed(await decks.rename(deck.id, name.trim()));
      setEditing(false);
    });
  };

  return (
    <form onSubmit={onSubmit} className="inline-form" aria-label="Rename deck">
      <label className="field grow">
        <span>Deck name</span>
        <input
          required
          maxLength={500}
          value={name}
          autoFocus // focus follows the user's click on Rename
          onChange={(event) => {
            setName(event.target.value);
          }}
        />
      </label>
      <button type="submit" className="button primary" disabled={submit.busy || name.trim() === ""}>
        Save
      </button>
      <button
        type="button"
        className="button"
        onClick={() => {
          setEditing(false);
        }}
      >
        Cancel
      </button>
      <ErrorText message={submit.error} />
    </form>
  );
}

export function DeckDetailPage() {
  const { deckId = "" } = useParams();
  const navigate = useNavigate();
  const all = useResource(() => decks.list(), `decks:${deckId}`);
  const stats = useResource(() => decks.stats(deckId), `stats:${deckId}`);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  useDocumentTitle(all.data?.find((d) => d.id === deckId)?.name ?? "Deck");

  if (all.error !== null) {
    return <ErrorText message={all.error} />;
  }
  if (all.data === undefined) {
    return <Loading />;
  }
  const list = all.data;
  const node = flattenTree(buildDeckTree(list)).find((n) => n.deck.id === deckId);
  if (node === undefined) {
    return (
      <section>
        <h1>Deck not found</h1>
        <p>
          <Link to="/">Back to your decks</Link>
        </p>
      </section>
    );
  }

  const { deck, children } = node;
  const subdecks = descendantCount(node);
  const path = deckPath(list, deckId);

  const replaceDeck = (renamed: Deck): void => {
    all.setData(list.map((d) => (d.id === renamed.id ? renamed : d)));
  };

  const remove = async (): Promise<void> => {
    setDeleteError(null);
    try {
      await decks.remove(deckId);
      void navigate(deck.parent_id === null ? "/" : `/decks/${deck.parent_id}`);
    } catch (failure) {
      setDeleteError(describeError(failure));
    }
  };

  return (
    <section>
      <nav aria-label="Breadcrumb" className="breadcrumb">
        <Link to="/">Decks</Link>
        {path.map((ancestor) => (
          <span key={ancestor.id}>
            {" / "}
            <Link to={`/decks/${ancestor.id}`}>{ancestor.name}</Link>
          </span>
        ))}
      </nav>
      <h1>{deck.name}</h1>

      <ErrorText message={stats.error} />
      {stats.loading ? <Loading label="Loading statistics…" /> : null}
      {stats.data === undefined ? null : <StatsPanel stats={stats.data} />}

      <div className="row actions">
        <Link className="button primary" to={`/decks/${deckId}/study`}>
          Study now
        </Link>
        <Link className="button" to={`/decks/${deckId}/notes/new`}>
          Add card
        </Link>
        <Link className="button" to={`/decks/${deckId}/generate`}>
          Generate from a file
        </Link>
        <Link className="button" to={`/cards?deck=${deckId}`}>
          Browse cards
        </Link>
        <Link className="button" to={`/decks/${deckId}/exam`}>
          Mock exam
        </Link>
        <Link className="button" to={`/decks/${deckId}/share`}>
          Share
        </Link>
      </div>

      {children.length > 0 ? (
        <>
          <h2>Sub-decks</h2>
          <ul className="deck-tree">
            {children.map((child) => (
              <li key={child.deck.id}>
                <Link to={`/decks/${child.deck.id}`}>{child.deck.name}</Link>
              </li>
            ))}
          </ul>
        </>
      ) : null}

      <Suspense fallback={null}>
        <DeckSummaryPanel key={deckId} deckId={deckId} />
        <DeckChatPanel key={`chat:${deckId}`} deckId={deckId} />
      </Suspense>

      <h2>Manage</h2>
      <CreateDeckForm all={all.data} initialParent={deckId} onCreated={all.reload} />
      <div className="row actions">
        <RenameForm deck={deck} onRenamed={replaceDeck} />
        <ConfirmButton
          label="Delete deck"
          confirmLabel="Delete for good"
          prompt={
            subdecks > 0
              ? `Delete “${deck.name}”, its ${subdecks} sub-deck${subdecks === 1 ? "" : "s"} and every card in them? This cannot be undone.`
              : `Delete “${deck.name}” and every card in it? This cannot be undone.`
          }
          onConfirm={remove}
        />
      </div>
      <ErrorText message={deleteError} />
      <ExportDeck deckId={deckId} />
    </section>
  );
}
