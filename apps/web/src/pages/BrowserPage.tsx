// SPDX-License-Identifier: AGPL-3.0-only
// The card browser (F-DECK-2, F-DECK-3): filter, search, sort, page, select and
// apply bulk actions. Filters live in the URL so a view can be bookmarked. A
// card's text opens a read-only view of it, as it is studied.

import { useState, type SubmitEvent } from "react";
import { Link, useSearchParams } from "react-router";

import { cards, decks } from "../api/endpoints";
import {
  CARD_STATES,
  SORT_KEYS,
  type BrowserPage as BrowserPageData,
  type BrowserQuery,
  type BulkAction,
  type BulkRequest,
  type CardState,
  type SortKey,
} from "../api/types";
import { ErrorText, Loading } from "../components/ui";
import { deckOptions, ownedDecks } from "../lib/deckTree";
import { parseTags } from "../lib/text";
import { useResource } from "../lib/useResource";
import { useSubmit } from "../lib/useSubmit";
import { useDocumentTitle } from "../lib/useDocumentTitle";
import { BrowserCardView } from "./BrowserCardView";

const PAGE_SIZE = 50;

const ACTIONS: { value: BulkAction; label: string }[] = [
  { value: "move", label: "Move to deck" },
  { value: "add_tags", label: "Add tags" },
  { value: "remove_tags", label: "Remove tags" },
  { value: "suspend", label: "Suspend" },
  { value: "unsuspend", label: "Unsuspend" },
  { value: "bury", label: "Bury for a day" },
  { value: "unbury", label: "Unbury" },
  { value: "flag", label: "Set flag" },
  { value: "delete", label: "Delete" },
];

const SORT_LABELS: Record<SortKey, string> = {
  created: "Date added",
  due: "Due date",
  proficiency: "Proficiency",
  deck: "Deck",
};

function isCardState(value: string): value is CardState {
  return (CARD_STATES as readonly string[]).includes(value);
}

function isSortKey(value: string): value is SortKey {
  return (SORT_KEYS as readonly string[]).includes(value);
}

export type ParsedQuery = BrowserQuery & { state: CardState[]; offset: number };

export function queryFromParams(params: URLSearchParams): ParsedQuery {
  const sort = params.get("sort") ?? "created";
  const flag = params.get("flag");
  const page = Math.max(0, Number(params.get("page") ?? "0") || 0);
  const query: ParsedQuery = {
    state: params.getAll("state").filter(isCardState),
    sort: isSortKey(sort) ? sort : "created",
    order: params.get("order") === "desc" ? "desc" : "asc",
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  };
  const deck = params.get("deck");
  if (deck !== null && deck !== "") {
    query.deck_id = deck;
  }
  const tag = params.get("tag");
  if (tag !== null && tag !== "") {
    query.tag = tag;
  }
  const q = params.get("q");
  if (q !== null && q !== "") {
    query.q = q;
  }
  if (flag !== null && flag !== "" && /^[0-7]$/.test(flag)) {
    query.flag = Number(flag);
  }
  return query;
}

function withoutDeck(query: ParsedQuery): ParsedQuery {
  const rest = { ...query };
  delete rest.deck_id;
  return rest;
}

/** An answer that never comes, while a deck filter waits to be checked. */
const WAITING = new Promise<BrowserPageData>(() => undefined);

function BulkBar({
  selected,
  deckChoices,
  onDone,
}: {
  selected: string[];
  deckChoices: { id: string; label: string }[];
  onDone: (message: string) => void;
}) {
  const [action, setAction] = useState<BulkAction>("move");
  const [deckId, setDeckId] = useState("");
  const [tags, setTags] = useState("");
  const [flag, setFlag] = useState("1");
  const [confirming, setConfirming] = useState(false);
  const submit = useSubmit();

  const apply = (): void => {
    const body: BulkRequest = { card_ids: selected, action };
    if (action === "move") {
      body.deck_id = deckId;
    } else if (action === "add_tags" || action === "remove_tags") {
      body.tags = parseTags(tags);
    } else if (action === "flag") {
      body.flag = Number(flag);
    }
    void submit.run(async () => {
      const result = await cards.bulk(body);
      setConfirming(false);
      onDone(`Updated ${result.cards} card${result.cards === 1 ? "" : "s"}.`);
    });
  };

  const onSubmit = (event: SubmitEvent): void => {
    event.preventDefault();
    if (action === "delete" && !confirming) {
      setConfirming(true);
      return;
    }
    apply();
  };

  const missing =
    (action === "move" && deckId === "") ||
    ((action === "add_tags" || action === "remove_tags") && parseTags(tags).length === 0);

  return (
    <form className="bulk-bar" onSubmit={onSubmit} aria-label="Bulk actions">
      <strong>{selected.length} selected</strong>
      <label className="field">
        <span>Action</span>
        <select
          value={action}
          onChange={(event) => {
            setAction(event.target.value as BulkAction);
            setConfirming(false);
          }}
        >
          {ACTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </label>
      {action === "move" ? (
        <label className="field">
          <span>Deck</span>
          <select
            value={deckId}
            onChange={(event) => {
              setDeckId(event.target.value);
            }}
          >
            <option value="">Choose a deck</option>
            {deckChoices.map((option) => (
              <option key={option.id} value={option.id}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      {action === "add_tags" || action === "remove_tags" ? (
        <label className="field">
          <span>Tags</span>
          <input
            value={tags}
            onChange={(event) => {
              setTags(event.target.value);
            }}
          />
        </label>
      ) : null}
      {action === "flag" ? (
        <label className="field">
          <span>Flag</span>
          <select
            value={flag}
            onChange={(event) => {
              setFlag(event.target.value);
            }}
          >
            <option value="0">No flag</option>
            {[1, 2, 3, 4, 5, 6, 7].map((n) => (
              <option key={n} value={String(n)}>
                Flag {n}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      {confirming ? (
        <span role="alert">
          Delete the notes behind {selected.length} card{selected.length === 1 ? "" : "s"}? Every card of those notes
          goes too.
          {/* Only an admin browses, and an admin's cards are in every member's library (ADR 0024). */}
          {" They are in every member's library: members who studied them lose their progress on them."}
        </span>
      ) : null}
      <button
        type="submit"
        className={action === "delete" ? "button danger" : "button primary"}
        disabled={submit.busy || missing}
      >
        {confirming ? "Delete for good" : "Apply"}
      </button>
      <ErrorText message={submit.error} />
    </form>
  );
}

export function BrowserPage() {
  useDocumentTitle("Browse cards");
  const [params, setParams] = useSearchParams();
  const deckList = useResource(() => decks.list(), "decks");
  const asked = queryFromParams(params);
  // A deck filter must name one of the user's own decks: a library deck's cards
  // are not in the browser, so its filter is dropped and the user's own cards are
  // listed. Until the decks are known the filter cannot be checked, so nothing is
  // asked for yet; if they cannot load, the server decides.
  const ownIds = new Set(ownedDecks(deckList.data ?? []).map((deck) => deck.id));
  const checking = asked.deck_id !== undefined && deckList.data === undefined && deckList.error === null;
  const ignoredDeck = asked.deck_id !== undefined && deckList.data !== undefined && !ownIds.has(asked.deck_id);
  const query: ParsedQuery = ignoredDeck ? withoutDeck(asked) : asked;
  const queryKey = checking ? "checking" : JSON.stringify(query);
  const page = useResource(() => (checking ? WAITING : cards.browse(query)), queryKey);
  const [selected, setSelected] = useState<string[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [search, setSearch] = useState(params.get("q") ?? "");
  const [tag, setTag] = useState(params.get("tag") ?? "");
  // The position, in this result page, of the card being viewed.
  const [viewing, setViewing] = useState<number | null>(null);

  // The browser covers the user's own cards only, so it offers only their decks.
  const deckChoices = deckOptions(ownedDecks(deckList.data ?? []));
  const rows = page.data?.cards ?? [];
  const total = page.data?.total ?? 0;
  const offset = query.offset;

  const update = (changes: Record<string, string | string[] | null>): void => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(changes)) {
      next.delete(key);
      if (Array.isArray(value)) {
        value.forEach((item) => {
          next.append(key, item);
        });
      } else if (value !== null && value !== "") {
        next.set(key, value);
      }
    }
    if (!("page" in changes)) {
      next.delete("page");
    }
    setSelected([]);
    setParams(next);
  };

  const toggleState = (state: CardState): void => {
    const current = query.state;
    update({ state: current.includes(state) ? current.filter((s) => s !== state) : [...current, state] });
  };

  const allSelected = rows.length > 0 && rows.every((row) => selected.includes(row.card_id));

  return (
    <section>
      <h1>Browse cards</h1>
      <form
        className="filters"
        aria-label="Filters"
        onSubmit={(event) => {
          event.preventDefault();
          update({ q: search.trim(), tag: tag.trim() });
        }}
      >
        <label className="field grow">
          <span>Search</span>
          <input
            type="search"
            value={search}
            maxLength={200}
            onChange={(event) => {
              setSearch(event.target.value);
            }}
          />
        </label>
        <label className="field">
          <span>Deck</span>
          <select
            value={query.deck_id ?? ""}
            onChange={(event) => {
              update({ deck: event.target.value });
            }}
          >
            <option value="">All decks</option>
            {deckChoices.map((option) => (
              <option key={option.id} value={option.id}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Tag</span>
          <input
            value={tag}
            maxLength={64}
            onChange={(event) => {
              setTag(event.target.value);
            }}
          />
        </label>
        <label className="field">
          <span>Flag</span>
          <select
            value={query.flag === undefined ? "" : String(query.flag)}
            onChange={(event) => {
              update({ flag: event.target.value });
            }}
          >
            <option value="">Any</option>
            <option value="0">No flag</option>
            {[1, 2, 3, 4, 5, 6, 7].map((n) => (
              <option key={n} value={String(n)}>
                Flag {n}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Sort by</span>
          <select
            value={query.sort}
            onChange={(event) => {
              update({ sort: event.target.value });
            }}
          >
            {SORT_KEYS.map((key) => (
              <option key={key} value={key}>
                {SORT_LABELS[key]}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Order</span>
          <select
            value={query.order}
            onChange={(event) => {
              update({ order: event.target.value });
            }}
          >
            <option value="asc">Ascending</option>
            <option value="desc">Descending</option>
          </select>
        </label>
        <fieldset className="chips">
          <legend>State</legend>
          {CARD_STATES.map((state) => (
            <label key={state}>
              <input
                type="checkbox"
                checked={query.state.includes(state)}
                onChange={() => {
                  toggleState(state);
                }}
              />
              {state}
            </label>
          ))}
        </fieldset>
        <button type="submit" className="button">
          Search
        </button>
      </form>

      {ignoredDeck ? (
        <p className="muted">
          That deck is in the library, so its cards are not listed here; showing your own cards. You can{" "}
          <Link to={`/decks/${String(asked.deck_id)}/read`}>read its cards</Link> instead.
        </p>
      ) : null}
      <p role="status" className="muted">
        {message}
      </p>
      {selected.length > 0 ? (
        <BulkBar
          selected={selected}
          deckChoices={deckChoices}
          onDone={(text) => {
            setMessage(text);
            setSelected([]);
            page.reload();
          }}
        />
      ) : null}

      <ErrorText message={page.error} />
      {page.loading ? <Loading /> : null}
      {!page.loading && page.error === null && rows.length === 0 ? (
        <p className="empty">No cards match these filters.</p>
      ) : null}
      {rows.length > 0 ? (
        <div className="table-wrap">
          <table className="browser">
            <thead>
              <tr>
                <th scope="col">
                  <input
                    type="checkbox"
                    aria-label="Select all on this page"
                    checked={allSelected}
                    onChange={() => {
                      setSelected(allSelected ? [] : rows.map((row) => row.card_id));
                    }}
                  />
                </th>
                <th scope="col">Card</th>
                <th scope="col">Deck</th>
                <th scope="col">State</th>
                <th scope="col">Tags</th>
                <th scope="col">Proficiency</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, position) => {
                const title = row.preview === "" ? "(empty)" : row.preview;
                return (
                  <tr key={row.card_id}>
                    <td>
                      <input
                        type="checkbox"
                        aria-label={`Select ${title}`}
                        checked={selected.includes(row.card_id)}
                        onChange={() => {
                          setSelected((current) =>
                            current.includes(row.card_id)
                              ? current.filter((id) => id !== row.card_id)
                              : [...current, row.card_id],
                          );
                        }}
                      />
                    </td>
                    <td>
                      <button
                        type="button"
                        className="link-button card-open"
                        aria-haspopup="dialog"
                        onClick={() => {
                          setViewing(position);
                        }}
                      >
                        {title}
                      </button>
                      {row.flag > 0 ? <span className="flag"> flag {row.flag}</span> : null}
                    </td>
                    <td>{row.deck_name}</td>
                    <td>{row.state}</td>
                    <td>{row.tags.join(" ")}</td>
                    <td>{row.proficiency}%</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
      {viewing === null ? null : (
        <BrowserCardView
          rows={rows}
          index={viewing}
          onMove={setViewing}
          onClose={() => {
            setViewing(null);
          }}
        />
      )}
      {total > PAGE_SIZE ? (
        <nav className="row pager" aria-label="Pages">
          <button
            type="button"
            className="button"
            disabled={offset === 0}
            onClick={() => {
              update({ page: String(offset / PAGE_SIZE - 1) });
            }}
          >
            Previous
          </button>
          <span>
            {offset + 1} to {Math.min(offset + PAGE_SIZE, total)} of {total}
          </span>
          <button
            type="button"
            className="button"
            disabled={offset + PAGE_SIZE >= total}
            onClick={() => {
              update({ page: String(offset / PAGE_SIZE + 1) });
            }}
          >
            Next
          </button>
        </nav>
      ) : null}
    </section>
  );
}
