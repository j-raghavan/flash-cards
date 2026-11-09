// SPDX-License-Identifier: AGPL-3.0-only
// Small shared pieces: error text, loading text, an inline confirm and the
// proficiency ring.

import { useEffect, useRef, useState, type ReactNode } from "react";

import { renderMarkdown } from "../lib/markdown";

export function ErrorText({ message }: { message: string | null }) {
  if (message === null) {
    return null;
  }
  return (
    <p className="error" role="alert">
      {message}
    </p>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <p className="muted" role="status">
      {label}
    </p>
  );
}

/** Rendered, sanitized card text. */
export function CardContent({ markdown, className }: { markdown: string; className?: string }) {
  return (
    <div
      className={className === undefined ? "card-content" : `card-content ${className}`}
      // renderMarkdown sanitizes its output with DOMPurify.
      dangerouslySetInnerHTML={{ __html: renderMarkdown(markdown) }}
    />
  );
}

interface ConfirmProps {
  label: string;
  prompt: ReactNode;
  confirmLabel: string;
  onConfirm: () => Promise<void> | void;
}

/** A button that asks once more, inline, before a destructive action. */
export function ConfirmButton({ label, prompt, confirmLabel, onConfirm }: ConfirmProps) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const cancelRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (asking) {
      cancelRef.current?.focus();
    }
  }, [asking]);

  if (!asking) {
    return (
      <button
        type="button"
        className="button danger-text"
        onClick={() => {
          setAsking(true);
        }}
      >
        {label}
      </button>
    );
  }
  return (
    <div className="confirm" role="alertdialog" aria-label={label}>
      <p>{prompt}</p>
      <div className="row">
        <button
          type="button"
          className="button danger"
          disabled={busy}
          onClick={() => {
            setBusy(true);
            void Promise.resolve(onConfirm()).finally(() => {
              setBusy(false);
              setAsking(false);
            });
          }}
        >
          {confirmLabel}
        </button>
        <button
          type="button"
          className="button"
          ref={cancelRef}
          onClick={() => {
            setAsking(false);
          }}
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

/** Deck proficiency (0 to 100) as a ring (F-PROG-2). */
export function ProficiencyRing({ value, size = 96 }: { value: number; size?: number }) {
  const clamped = Math.max(0, Math.min(100, Math.round(value)));
  const stroke = 8;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  return (
    <figure className="ring" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label={`Proficiency ${clamped}%`}>
        <circle className="ring-track" cx={size / 2} cy={size / 2} r={radius} strokeWidth={stroke} fill="none" />
        <circle
          className="ring-value"
          cx={size / 2}
          cy={size / 2}
          r={radius}
          strokeWidth={stroke}
          fill="none"
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={circumference * (1 - clamped / 100)}
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
        />
      </svg>
      <figcaption aria-hidden="true">{clamped}%</figcaption>
    </figure>
  );
}
