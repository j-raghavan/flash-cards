// SPDX-License-Identifier: AGPL-3.0-only
// Small shared pieces: error text, loading text, an inline confirm and the
// proficiency ring.

import { useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode } from "react";

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

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * A modal dialog: focus moves into it when it opens, Tab and Shift+Tab stay
 * inside it, Escape closes it, and focus returns to where it was on close.
 */
export function Dialog({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();

  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    ref.current?.focus();
    return () => {
      previous?.focus();
    };
  }, []);

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>): void => {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      onClose();
      return;
    }
    if (event.key !== "Tab" || ref.current === null) {
      return;
    }
    // The Close button is always there, so the list is never empty.
    const focusable = [...ref.current.querySelectorAll<HTMLElement>(FOCUSABLE)];
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    const active = document.activeElement;
    if (event.shiftKey && (active === first || active === ref.current)) {
      event.preventDefault();
      last?.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first?.focus();
    }
  };

  return (
    <div className="dialog-backdrop">
      <div
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        ref={ref}
        onKeyDown={onKeyDown}
      >
        <div className="dialog-header">
          <h2 id={titleId}>{title}</h2>
          <button type="button" className="button" onClick={onClose}>
            Close
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

/** A read-only value, such as a link shown once, with a button that copies it. */
export function CopyField({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState<"idle" | "copied" | "failed">("idle");
  return (
    <>
      <div className="row">
        <input
          className="grow"
          readOnly
          aria-label={label}
          value={value}
          onFocus={(event) => {
            event.target.select();
          }}
        />
        <button
          type="button"
          className="button"
          onClick={() => {
            navigator.clipboard.writeText(value).then(
              () => {
                setCopied("copied");
              },
              () => {
                setCopied("failed");
              },
            );
          }}
        >
          Copy
        </button>
      </div>
      {copied === "copied" ? <p className="muted">Copied.</p> : null}
      {copied === "failed" ? <p className="muted">Could not copy; select the text and copy it by hand.</p> : null}
    </>
  );
}

/** Deck proficiency (0 to 100) as a ring (F-PROG-2). */
export function ProficiencyRing({
  value,
  size = 96,
  label = "Proficiency",
}: {
  value: number;
  size?: number;
  label?: string;
}) {
  const clamped = Math.max(0, Math.min(100, Math.round(value)));
  const stroke = 8;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  return (
    <figure className="ring" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label={`${label} ${String(clamped)}%`}>
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
