// SPDX-License-Identifier: AGPL-3.0-only
// Home: the deck tree and a form to add a deck (F-DECK-1).

import { useState, type SubmitEvent } from "react";
import { Link } from "react-router";

import { decks } from "../api/endpoints";
import type { Deck } from "../api/types";
import { ErrorText, Loading } from "../components/ui";
import { OnboardingPrompt } from "../onboarding/OnboardingPrompt";
import { buildDeckTree, deckOptions, type DeckNode } from "../lib/deckTree";
import { useResource } from "../lib/useResource";
import { useSubmit } from "../lib/useSubmit";
import { useDocumentTitle } from "../lib/useDocumentTitle";

function DeckTree({ nodes }: { nodes: DeckNode[] }) {
  return (
    <ul className="deck-tree">
      {nodes.map((node) => (
        <li key={node.deck.id}>
          <Link to={`/decks/${node.deck.id}`}>{node.deck.name}</Link>
          {node.children.length > 0 ? <DeckTree nodes={node.children} /> : null}
        </li>
      ))}
    </ul>
  );
}

/** A stable colour for a deck's tile, from its id. */
function deckTone(id: string): number {
  let hash = 0;
  for (const char of id) {
    hash = (hash * 31 + char.charCodeAt(0)) % 997;
  }
  return hash % TONES;
}

const TONES = 6;

function countDecks(nodes: DeckNode[]): number {
  return nodes.reduce((total, node) => total + 1 + countDecks(node.children), 0);
}

/** Top-level decks as tiles; their subdecks are listed inside. */
function DeckGrid({ nodes }: { nodes: DeckNode[] }) {
  return (
    <ul className="deck-tree deck-grid">
      {nodes.map((node) => {
        const subdecks = countDecks(node.children);
        return (
          <li key={node.deck.id} className="deck-tile">
            <span className={`deck-avatar tone-${String(deckTone(node.deck.id))}`} aria-hidden="true">
              {Array.from(node.deck.name.trim())[0]?.toUpperCase() ?? "#"}
            </span>
            <Link to={`/decks/${node.deck.id}`}>{node.deck.name}</Link>
            {subdecks > 0 ? (
              <p className="deck-meta">
                {subdecks} {subdecks === 1 ? "subdeck" : "subdecks"}
              </p>
            ) : null}
            {node.children.length > 0 ? <DeckTree nodes={node.children} /> : null}
          </li>
        );
      })}
    </ul>
  );
}

export function CreateDeckForm({
  all,
  initialParent = "",
  onCreated,
}: {
  all: Deck[];
  initialParent?: string;
  onCreated: (deck: Deck) => void;
}) {
  const [name, setName] = useState("");
  const [parent, setParent] = useState(initialParent);
  const submit = useSubmit();

  const onSubmit = (event: SubmitEvent): void => {
    event.preventDefault();
    void submit.run(async () => {
      const created = await decks.create(name.trim(), parent === "" ? null : parent);
      setName("");
      onCreated(created);
    });
  };

  return (
    <form onSubmit={onSubmit} className="inline-form" aria-label="Add a deck">
      <label className="field grow">
        <span>New deck</span>
        <input
          required
          maxLength={500}
          value={name}
          placeholder="e.g. Organic Chemistry"
          onChange={(event) => {
            setName(event.target.value);
          }}
        />
      </label>
      {all.length > 0 ? (
        <label className="field">
          <span>Inside</span>
          <select
            value={parent}
            onChange={(event) => {
              setParent(event.target.value);
            }}
          >
            <option value="">No parent</option>
            {deckOptions(all).map((option) => (
              <option key={option.id} value={option.id}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      <button type="submit" className="button primary" disabled={submit.busy || name.trim() === ""}>
        Add deck
      </button>
      <ErrorText message={submit.error} />
    </form>
  );
}

export function DeckListPage() {
  useDocumentTitle("Decks");
  const list = useResource(() => decks.list(), "decks");

  return (
    <section>
      <div className="page-head">
        <div>
          <h1>Decks</h1>
          <p className="muted">Pick a deck to study, add cards or generate them with AI.</p>
        </div>
        <p className="row">
          <Link className="button" to="/import">
            Import decks from Anki
          </Link>
          <Link className="button" to="/note-types">
            Custom note types
          </Link>
        </p>
      </div>
      {list.loading && list.data === undefined ? <Loading /> : null}
      <ErrorText message={list.error} />
      {list.data === undefined ? null : (
        <>
          <OnboardingPrompt deckCount={list.data.length} />
          {list.data.length === 0 ? (
            <p className="empty">
              No decks yet. Create one below, then add cards by hand or generate them from a PDF or image. The{" "}
              <Link to="/welcome">guided setup</Link> walks you through it.
            </p>
          ) : (
            <DeckGrid nodes={buildDeckTree(list.data)} />
          )}
          <div className="panel new-deck">
            <h2>New deck</h2>
            <CreateDeckForm all={list.data} onCreated={list.reload} />
          </div>
        </>
      )}
    </section>
  );
}
