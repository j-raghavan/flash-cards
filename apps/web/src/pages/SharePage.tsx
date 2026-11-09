// SPDX-License-Identifier: AGPL-3.0-only
// Deck sharing (F-SHARE-1..3): share links for a deck, with copying allowed or
// not, revocable at any time; and the page a link opens, which previews the
// shared deck and, when allowed, copies it into the reader's decks.
//
// A link carries its token in the URL fragment (/shared#token=...), which the
// browser never sends to a server, so tokens stay out of access logs.

import { useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router";

import { describeError } from "../api/client";
import { decks, shares } from "../api/endpoints";
import type { CreatedShareLink, ShareLink, SharedNote } from "../api/types";
import { CardContent, ConfirmButton, CopyField, ErrorText, Loading } from "../components/ui";
import { cardSides } from "../lib/cardSides";
import { useDocumentTitle } from "../lib/useDocumentTitle";
import { useResource } from "../lib/useResource";
import { useSubmit } from "../lib/useSubmit";

export function shareUrl(token: string): string {
  return `${window.location.origin}/shared#token=${encodeURIComponent(token)}`;
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

function NewLink({ link }: { link: CreatedShareLink }) {
  return (
    <div className="panel" role="status">
      <p>
        <strong>Copy this link now.</strong> It is shown only once; anyone signed in who has it can{" "}
        {link.allow_clone ? "preview and copy" : "preview"} the deck.
      </p>
      <CopyField label="Share link" value={shareUrl(link.token)} />
    </div>
  );
}

function LinkRow({ link, onChanged }: { link: ShareLink; onChanged: (link: ShareLink) => void }) {
  const toggle = useSubmit();
  const [revokeError, setRevokeError] = useState<string | null>(null);
  const revoked = link.revoked_at !== null;
  return (
    <tr>
      <td>{formatDate(link.created_at)}</td>
      <td>
        <label className="row">
          <input
            type="checkbox"
            checked={link.allow_clone}
            disabled={revoked || toggle.busy}
            onChange={(event) => {
              const allow = event.target.checked;
              void toggle.run(async () => {
                onChanged(await shares.setAllowClone(link.id, allow));
              });
            }}
          />
          <span className="visually-hidden">Allow copying the link created {formatDate(link.created_at)}</span>
          <span aria-hidden="true">{link.allow_clone ? "Allowed" : "Not allowed"}</span>
        </label>
        <ErrorText message={toggle.error} />
      </td>
      <td>{link.revoked_at === null ? "Active" : `Revoked ${formatDate(link.revoked_at)}`}</td>
      <td>
        {revoked ? null : (
          <ConfirmButton
            label="Revoke"
            confirmLabel="Revoke link"
            prompt="Revoke this link? It stops working at once; copies already made are kept."
            onConfirm={async () => {
              setRevokeError(null);
              try {
                onChanged(await shares.revoke(link.id));
              } catch (failure) {
                setRevokeError(describeError(failure));
              }
            }}
          />
        )}
        <ErrorText message={revokeError} />
      </td>
    </tr>
  );
}

export function SharePage() {
  const { deckId = "" } = useParams();
  const deck = useResource(() => decks.get(deckId), `deck:${deckId}`);
  const links = useResource(() => shares.list(deckId), `shares:${deckId}`);
  const [allowClone, setAllowClone] = useState(false);
  const [created, setCreated] = useState<CreatedShareLink | null>(null);
  const create = useSubmit();
  useDocumentTitle(deck.data === undefined ? "Share deck" : `Share ${deck.data.name}`);

  const replace = (changed: ShareLink): void => {
    links.setData((links.data ?? []).map((l) => (l.id === changed.id ? changed : l)));
  };

  return (
    <section>
      <nav aria-label="Breadcrumb" className="breadcrumb">
        <Link to={`/decks/${deckId}`}>Back to deck</Link>
      </nav>
      <h1>Share {deck.data === undefined ? "deck" : `“${deck.data.name}”`}</h1>
      <p className="muted">
        A share link lets anyone signed in to this app preview the deck and its sub-decks. Revoke a link to stop it
        working.
      </p>
      <ErrorText message={deck.error} />

      <h2>New link</h2>
      <div className="stack">
        <label className="row">
          <input
            type="checkbox"
            checked={allowClone}
            onChange={(event) => {
              setAllowClone(event.target.checked);
            }}
          />
          Allow people with the link to copy the deck into their own decks
        </label>
        <div className="row">
          <button
            type="button"
            className="button primary"
            disabled={create.busy}
            onClick={() => {
              void create.run(async () => {
                const link = await shares.create(deckId, allowClone);
                setCreated(link);
                links.setData([link, ...(links.data ?? [])]);
              });
            }}
          >
            Create link
          </button>
        </div>
        <ErrorText message={create.error} />
        {created === null ? null : <NewLink link={created} />}
      </div>

      <h2>Links</h2>
      <ErrorText message={links.error} />
      {links.data === undefined ? (
        links.error === null ? (
          <Loading />
        ) : null
      ) : links.data.length === 0 ? (
        <p className="empty">No links yet.</p>
      ) : (
        <div className="table-wrap">
          <table className="browser">
            <thead>
              <tr>
                <th scope="col">Created</th>
                <th scope="col">Copying</th>
                <th scope="col">Status</th>
                <th scope="col">
                  <span className="visually-hidden">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {links.data.map((link) => (
                <LinkRow key={link.id} link={link} onChanged={replace} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

/** One note of a shared deck, rendered through the same sanitizer as any card. */
function SharedNotePreview({ note }: { note: SharedNote }) {
  if (note.note_type === "custom") {
    return (
      <dl className="shared-fields">
        {Object.entries(note.fields).map(([name, value]) => (
          <div key={name}>
            <dt>{name}</dt>
            <dd>
              <CardContent markdown={value} />
            </dd>
          </div>
        ))}
      </dl>
    );
  }
  const sides = cardSides(note.note_type, note.fields, 1);
  return (
    <>
      <CardContent markdown={sides.front} />
      {sides.back === "" ? null : (
        <>
          <hr />
          <CardContent markdown={sides.back} className="muted" />
        </>
      )}
    </>
  );
}

export function SharedDeckPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const token = new URLSearchParams(location.hash.replace(/^#/, "")).get("token") ?? "";
  const preview = useResource(() => shares.preview(token), `shared:${token}`);
  const clone = useSubmit();
  useDocumentTitle(preview.data?.name ?? "Shared deck");

  if (token === "") {
    return (
      <section>
        <h1>Shared deck</h1>
        <p>This link is incomplete. Ask the person who shared it for the full link.</p>
      </section>
    );
  }
  if (preview.error !== null) {
    return (
      <section>
        <h1>Shared deck</h1>
        <ErrorText message={preview.error} />
        <p className="muted">The link may have been revoked.</p>
      </section>
    );
  }
  if (preview.data === undefined) {
    return <Loading />;
  }
  const shared = preview.data;

  return (
    <section>
      <p className="muted">Shared deck</p>
      <h1>{shared.name}</h1>
      <p>
        {shared.note_count} note{shared.note_count === 1 ? "" : "s"} in {shared.deck_count} deck
        {shared.deck_count === 1 ? "" : "s"}.
      </p>
      {shared.allow_clone ? (
        <div className="row actions">
          <button
            type="button"
            className="button primary"
            disabled={clone.busy}
            onClick={() => {
              void clone.run(async () => {
                const copy = await shares.clone(token);
                void navigate(`/decks/${copy.id}`);
              });
            }}
          >
            {clone.busy ? "Copying…" : "Copy to my decks"}
          </button>
        </div>
      ) : (
        <p className="muted">The owner has not allowed copying this deck.</p>
      )}
      <ErrorText message={clone.error} />

      {shared.decks.length > 1 ? (
        <>
          <h2>Decks</h2>
          <ul className="plain-list">
            {shared.decks.map((node, index) => (
              <li key={index} style={{ marginLeft: `${String(Math.min(node.depth, 6) * 16)}px` }}>
                {node.name}
              </li>
            ))}
          </ul>
        </>
      ) : null}

      <h2>Notes</h2>
      {shared.notes.length < shared.note_count ? (
        <p className="muted">
          Showing the first {shared.notes.length} of {shared.note_count}.
        </p>
      ) : null}
      {shared.notes.length === 0 ? (
        <p className="empty">This deck has no notes yet.</p>
      ) : (
        <ol className="drafts">
          {shared.notes.map((note, index) => (
            <li key={index} className="draft chosen">
              <p className="muted small">{shared.decks[note.deck_index]?.name}</p>
              <SharedNotePreview note={note} />
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
