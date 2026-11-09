// SPDX-License-Identifier: AGPL-3.0-only
// Chat with a deck's source documents (F-AI-8). Each answer is AI output: it is
// labelled, rendered through CardContent, and lists the file and page it cites.
// Answers arrive in a live log, and asking never blocks the rest of the page.

import { useEffect, useRef, useState, type KeyboardEvent, type SubmitEvent } from "react";
import { Link } from "react-router";

import { aiErrorMessage, deckChat, isAbort, type Citation } from "../api/aiStudy";
import { ApiError } from "../api/client";
import { CardContent } from "../components/ui";
import { AiLabel } from "./shared";

const MAX_QUESTION = 2000;

interface Turn {
  id: number;
  question: string;
  answer?: string;
  citations?: Citation[];
  error?: string;
}

/** One entry per file and page, in the order the answer cites them. */
function distinctCitations(citations: Citation[]): Citation[] {
  const seen = new Set<string>();
  return citations.filter((c) => {
    const key = `${c.file_id}\u0000${String(c.page)}`;
    if (seen.has(key)) {
      return false;
    }
    seen.add(key);
    return true;
  });
}

function NoSources({ deckId }: { deckId: string }) {
  return (
    <div className="empty" role="alert">
      <p>This deck has no source documents to chat with yet.</p>
      <p>
        Chat answers from the PDFs and images you generate cards from.{" "}
        <Link to={`/decks/${deckId}/generate`}>Generate cards from a file</Link>, then ask your question here.
      </p>
    </div>
  );
}

export function DeckChatPanel({ deckId }: { deckId: string }) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [noSources, setNoSources] = useState(false);
  const nextId = useRef(1);
  // Files are numbered in the order answers first cite them, so the same file
  // keeps its number for the whole conversation.
  const [files, setFiles] = useState<string[]>([]);
  const controller = useRef<AbortController | null>(null);
  const boxRef = useRef<HTMLTextAreaElement>(null);

  useEffect(
    () => () => {
      controller.current?.abort();
    },
    [],
  );

  const ask = (): void => {
    const text = question.trim();
    if (text === "" || asking) {
      return;
    }
    const id = nextId.current++;
    const current = new AbortController();
    controller.current = current;
    setAsking(true);
    setQuestion("");
    // A click on Ask returns the cursor to the box for the next question.
    boxRef.current?.focus();
    setTurns((all) => [...all, { id, question: text }]);
    deckChat.ask(deckId, text, current.signal).then(
      (reply) => {
        if (current.signal.aborted) {
          return;
        }
        setAsking(false);
        setNoSources(false);
        setFiles((known) => [
          ...known,
          ...distinctCitations(reply.citations)
            .map((c) => c.file_id)
            .filter((file, index, list) => !known.includes(file) && list.indexOf(file) === index),
        ]);
        setTurns((all) =>
          all.map((turn) => (turn.id === id ? { ...turn, answer: reply.answer, citations: reply.citations } : turn)),
        );
      },
      (failure: unknown) => {
        if (isAbort(failure)) {
          return;
        }
        setAsking(false);
        if (failure instanceof ApiError && failure.status === 409) {
          // No sources: the question goes back in the box, ready for later.
          setNoSources(true);
          setTurns((all) => all.filter((turn) => turn.id !== id));
          setQuestion(text);
          return;
        }
        const message = aiErrorMessage(failure);
        setTurns((all) => all.map((turn) => (turn.id === id ? { ...turn, error: message } : turn)));
      },
    );
  };

  const onSubmit = (event: SubmitEvent): void => {
    event.preventDefault();
    ask();
  };

  // Enter asks; Shift+Enter starts a new line.
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>): void => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      ask();
    }
  };

  return (
    <section className="panel" aria-labelledby="deck-chat-heading">
      <h2 id="deck-chat-heading">Ask your source documents</h2>
      <p className="muted">Answers come from the files this deck was generated from, with the page they cite.</p>
      {noSources ? <NoSources deckId={deckId} /> : null}
      <ol className="chat-log" role="log" aria-live="polite" aria-label="Conversation">
        {turns.map((turn) => (
          <li key={turn.id} className="chat-turn">
            <p className="chat-question">
              <span className="visually-hidden">You asked: </span>
              {turn.question}
            </p>
            {turn.answer !== undefined ? (
              <div className="chat-answer">
                <AiLabel>AI-generated answer. Check the cited pages.</AiLabel>
                <CardContent aiOutput markdown={turn.answer} />
                {turn.citations !== undefined && turn.citations.length > 0 ? (
                  <ul className="citations" aria-label="Sources">
                    {distinctCitations(turn.citations).map((c) => (
                      <li key={`${c.file_id}:${String(c.page)}`}>
                        Document {files.indexOf(c.file_id) + 1}, page {c.page}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="muted">No pages cited.</p>
                )}
              </div>
            ) : turn.error !== undefined ? (
              <p className="error">{turn.error}</p>
            ) : (
              <p className="muted">Looking through your documents…</p>
            )}
          </li>
        ))}
      </ol>
      <form className="stack" onSubmit={onSubmit} aria-label="Ask a question">
        <label className="field">
          <span>Your question</span>
          <textarea
            ref={boxRef}
            rows={2}
            maxLength={MAX_QUESTION}
            value={question}
            aria-describedby="deck-chat-hint"
            onChange={(event) => {
              setQuestion(event.target.value);
            }}
            onKeyDown={onKeyDown}
          />
        </label>
        <p id="deck-chat-hint" className="muted">
          Enter asks; Shift+Enter starts a new line.
        </p>
        <div>
          <button type="submit" className="button" disabled={asking || question.trim() === ""}>
            {asking ? "Asking…" : "Ask"}
          </button>
        </div>
      </form>
    </section>
  );
}
