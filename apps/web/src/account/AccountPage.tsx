// SPDX-License-Identifier: AGPL-3.0-only
// The account page: email confirmation, signed-in devices (F-AUTH-2), the AI
// page quota, the AI data-use disclosure (F-LEGAL-2), data export and account
// deletion (F-IMP-5, NF-PRIV-1). Loaded on first visit.

import { startTransition, useEffect, useId, useRef, useState, type SubmitEvent } from "react";
import { useLocation, useNavigate } from "react-router";

import { account, forgetAiNotice, legal, safeTermsUrl, sessions, type AiQuota, type DeviceSession } from "../api/account";
import { ApiError, NetworkError, describeError } from "../api/client";
import { auth } from "../api/endpoints";
import type { User } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { ConfirmButton, Dialog, ErrorText, Loading } from "../components/ui";
import { discard } from "../offline/reviewQueue";
import { useDocumentTitle } from "../lib/useDocumentTitle";
import { useResource } from "../lib/useResource";
import { useSubmit } from "../lib/useSubmit";

/** How long the server waits before it sends another confirmation link. */
const RESEND_COOLDOWN_MS = 60_000;

function formatTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

/** "2026-11" as "November 2026". */
export function formatPeriod(period: string): string {
  const match = /^(\d{4})-(\d{2})$/.exec(period);
  if (match === null) {
    return period;
  }
  const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, 1));
  return date.toLocaleDateString(undefined, { month: "long", year: "numeric", timeZone: "UTC" });
}

// --- email confirmation ---

function EmailPanel({ user }: { user: User }) {
  const submit = useSubmit();
  const [sent, setSent] = useState(false);

  useEffect(() => {
    if (!sent) {
      return;
    }
    const timer = window.setTimeout(() => {
      setSent(false);
    }, RESEND_COOLDOWN_MS);
    return () => {
      window.clearTimeout(timer);
    };
  }, [sent]);

  return (
    <section className="panel" aria-labelledby="email-heading">
      <h2 id="email-heading">Email address</h2>
      <p>
        {user.email}{" "}
        <span className="muted">{user.email_verified ? "(confirmed)" : "(not confirmed yet)"}</span>
      </p>
      {user.email_verified ? null : (
        <>
          <p className="muted">Confirm your address so you can reset your password if you forget it.</p>
          <button
            type="button"
            className="button"
            disabled={sent || submit.busy}
            onClick={() => {
              void submit.run(async () => {
                await auth.resendVerification();
                setSent(true);
              });
            }}
          >
            Send the confirmation link again
          </button>
          <p role="status" className="muted">
            {sent
              ? "Check your inbox. A new link is sent at most once a minute, so wait a minute before asking again."
              : null}
          </p>
          <ErrorText message={submit.error} />
        </>
      )}
    </section>
  );
}

// --- signed-in devices ---

function SessionRow({ session, onRevoked }: { session: DeviceSession; onRevoked: (message: string) => void }) {
  const action = useSubmit();
  const lastActive = formatTime(session.last_seen_at ?? session.created_at);
  return (
    <li>
      <strong>{session.device}</strong>
      {session.current ? <span className="muted"> (this device)</span> : null}
      <br />
      <span className="muted">
        Last active {lastActive}. Signed in {formatTime(session.created_at)}.
      </span>
      {session.current ? null : (
        <div>
          <button
            type="button"
            className="button"
            disabled={action.busy}
            aria-label={`Sign out ${session.device}, last active ${lastActive}`}
            onClick={() => {
              void action.run(async () => {
                try {
                  await sessions.revoke(session.id);
                  onRevoked(`Signed out ${session.device}.`);
                } catch (failure) {
                  if (failure instanceof ApiError && failure.status === 404) {
                    onRevoked("That device was already signed out.");
                    return;
                  }
                  throw failure;
                }
              });
            }}
          >
            Sign out
          </button>
          <ErrorText message={action.error} />
        </div>
      )}
    </li>
  );
}

function SessionsPanel() {
  const list = useResource(sessions.list, "sessions");
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const others = (list.data ?? []).filter((session) => !session.current);
  const headingRef = useRef<HTMLHeadingElement>(null);

  const revoked = (message: string): void => {
    setError(null);
    setNote(message);
    list.reload();
    // The control that was used is about to disappear; keep focus in this section.
    headingRef.current?.focus();
  };

  return (
    <section className="panel" aria-labelledby="sessions-heading">
      <h2 id="sessions-heading" tabIndex={-1} ref={headingRef}>
        Signed-in devices
      </h2>
      {list.data === undefined && list.error === null ? <Loading /> : null}
      <ErrorText message={list.error} />
      {list.data === undefined ? null : (
        <ul className="plain-list">
          {list.data.map((session) => (
            <SessionRow key={session.id} session={session} onRevoked={revoked} />
          ))}
        </ul>
      )}
      {others.length > 0 ? (
        <ConfirmButton
          label="Sign out all other devices"
          confirmLabel="Sign out other devices"
          prompt="Sign out every device except this one? They will need to sign in again."
          onConfirm={async () => {
            setError(null);
            try {
              const result = await sessions.revokeOthers();
              revoked(`Signed out ${String(result.revoked)} other ${result.revoked === 1 ? "device" : "devices"}.`);
            } catch (failure) {
              setNote(null);
              setError(describeError(failure));
            }
          }}
        />
      ) : null}
      <p role="status" className="muted">
        {note}
      </p>
      <ErrorText message={error} />
    </section>
  );
}

// --- AI quota ---

function quotaText(quota: AiQuota): string {
  const month = formatPeriod(quota.period);
  if (quota.monthly_pages === 0) {
    return `AI generation is not included in your account for ${month}.`;
  }
  const used = Math.min(quota.used_pages, quota.monthly_pages);
  const left = quota.monthly_pages - used;
  const pages = (n: number): string => `${String(n)} ${n === 1 ? "page" : "pages"}`;
  if (left === 0) {
    return `You have used all ${pages(quota.monthly_pages)} for ${month}. The allowance renews at the start of next month.`;
  }
  return `You have used ${String(used)} of ${pages(quota.monthly_pages)} for ${month}, so ${pages(left)} are left.`;
}

function QuotaPanel() {
  const quota = useResource(account.aiQuota, "ai-quota");
  return (
    <section className="panel" aria-labelledby="quota-heading">
      <h2 id="quota-heading">AI pages this month</h2>
      {quota.loading ? <Loading /> : null}
      <ErrorText message={quota.error} />
      {quota.data === undefined ? null : (
        <>
          <p>{quotaText(quota.data)}</p>
          {quota.data.monthly_pages > 0 ? (
            <meter
              aria-label="AI pages used this month"
              min={0}
              max={quota.data.monthly_pages}
              value={Math.min(quota.data.used_pages, quota.data.monthly_pages)}
            />
          ) : null}
          <p className="muted">Each page of a file you generate cards from counts once.</p>
        </>
      )}
    </section>
  );
}

// --- AI data-use disclosure ---

function DisclosurePanel() {
  const disclosure = useResource(legal.aiDisclosure, "ai-disclosure");
  const provider = disclosure.data?.provider ?? null;
  const terms = safeTermsUrl(provider?.terms_url ?? null);
  const { hash } = useLocation();
  const ref = useRef<HTMLElement>(null);
  const loaded = disclosure.data !== undefined;
  useEffect(() => {
    // Arriving from the notice on the generate page: bring this section into view.
    if (loaded && hash === "#ai-data") {
      ref.current?.scrollIntoView();
    }
  }, [loaded, hash]);
  return (
    <section className="panel" id="ai-data" aria-labelledby="ai-data-heading" ref={ref}>
      <h2 id="ai-data-heading">How AI features use your content</h2>
      {disclosure.loading ? <Loading /> : null}
      <ErrorText message={disclosure.error} />
      {disclosure.data?.statements.map((statement) => (
        <p key={statement}>{statement}</p>
      ))}
      {provider === null ? null : (
        <p>
          AI provider on this server: {provider.name}.{" "}
          {terms === null ? null : (
            <a href={terms} target="_blank" rel="noopener noreferrer">
              Read {provider.name}&apos;s terms
            </a>
          )}
        </p>
      )}
    </section>
  );
}

// --- data export ---

const EXPORT_URL_LIFETIME_MS = 60_000;

function exportFileName(now: Date): string {
  return `flash-cards-export-${now.toISOString().slice(0, 10)}.json`;
}

/** Hand the browser a file to save. */
function saveJson(data: unknown, fileName: string): void {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  document.body.append(link);
  link.click();
  link.remove();
  // Some browsers cancel a download whose URL is revoked straight away.
  window.setTimeout(() => {
    URL.revokeObjectURL(url);
  }, EXPORT_URL_LIFETIME_MS);
}

function ExportPanel() {
  const submit = useSubmit();
  const [done, setDone] = useState(false);
  return (
    <section className="panel" aria-labelledby="export-heading">
      <h2 id="export-heading">Download your data</h2>
      <p className="muted">
        One JSON file with your decks, notes, cards, review history, goals, mock exams, share links and settings.
        Media is listed by id and type.
      </p>
      <button
        type="button"
        className="button"
        disabled={submit.busy}
        onClick={() => {
          setDone(false);
          void submit.run(async () => {
            // Only a successful answer becomes a file; an error is shown here.
            const data = await account.export();
            if (typeof data !== "object" || data === null) {
              throw new Error("The server sent an empty export. Try again.");
            }
            saveJson(data, exportFileName(new Date()));
            setDone(true);
          });
        }}
      >
        {submit.busy ? "Preparing…" : "Download my data"}
      </button>
      <p role="status" className="muted">
        {done ? "Your data has been downloaded." : null}
      </p>
      <ErrorText message={submit.error} />
    </section>
  );
}

// --- deletion ---

function sameEmail(typed: string, email: string): boolean {
  return typed.trim().toLowerCase() === email.trim().toLowerCase();
}

function DeleteDialog({ user, onClose }: { user: User; onClose: () => void }) {
  const { signedOut } = useAuth();
  const navigate = useNavigate();
  const [typed, setTyped] = useState("");
  const submit = useSubmit();
  const inputId = useId();
  const matches = sameEmail(typed, user.email);
  const running = useRef(false);
  // Once the request is on its way the dialog stays open, so its outcome is seen.
  const close = (): void => {
    if (!running.current) {
      onClose();
    }
  };

  const onSubmit = (event: SubmitEvent): void => {
    event.preventDefault();
    if (!matches || running.current) {
      return;
    }
    running.current = true;
    void submit.run(async () => {
      try {
        await account.remove();
      } catch (failure) {
        if (failure instanceof ApiError && failure.status === 409) {
          throw new Error(`${failure.message} Make another person an admin first, then delete your account.`, {
            cause: failure,
          });
        }
        if (failure instanceof NetworkError) {
          throw new Error(
            "Could not reach the server, so the account may not be deleted. Check your connection and try again.",
            { cause: failure },
          );
        }
        // 401: the session is gone, as after a deletion whose answer was lost
        // on the way back. The data on this device is cleared all the same.
        if (!(failure instanceof ApiError && failure.status === 401)) {
          throw failure;
        }
      } finally {
        running.current = false;
      }
      // The server has deleted the account and ended the session. Nothing of
      // it stays on this device: answers waiting to sync, the AI notice flag,
      // and (through signedOut) the remembered user and cached study data.
      try {
        await discard(user.id);
      } catch {
        // A browser without IndexedDB has no queue to clear.
      }
      forgetAiNotice(user.id);
      // The router navigates in a transition; forgetting the user in the same
      // transition keeps the signed-in frame from redirecting on its own first.
      startTransition(() => {
        void navigate("/login", { replace: true, state: { accountDeleted: true } });
        signedOut();
      });
    });
  };

  return (
    <Dialog title="Delete your account" onClose={close}>
      <form onSubmit={onSubmit} className="stack">
        <p>
          This permanently deletes your account with all its decks, cards, review history, goals, media and share
          links, and signs out every device. It cannot be undone.
        </p>
        <p className="muted">Download your data first if you want a copy.</p>
        <div className="field">
          <label htmlFor={inputId}>Type your email address to confirm</label>
          <input
            id={inputId}
            type="email"
            autoComplete="off"
            value={typed}
            aria-describedby={`${inputId}-hint`}
            onChange={(event) => {
              setTyped(event.target.value);
            }}
          />
          <small id={`${inputId}-hint`} className="muted">
            {user.email}
          </small>
        </div>
        <ErrorText message={submit.error} />
        <div className="row">
          <button type="submit" className="button danger" disabled={!matches || submit.busy}>
            {submit.busy ? "Deleting…" : "Delete my account permanently"}
          </button>
          <button type="button" className="button" disabled={submit.busy} onClick={close}>
            Cancel
          </button>
        </div>
      </form>
    </Dialog>
  );
}

function DeletePanel({ user }: { user: User }) {
  const [open, setOpen] = useState(false);
  return (
    <section className="panel" aria-labelledby="delete-heading">
      <h2 id="delete-heading" className="danger-text">
        Delete your account
      </h2>
      <p className="muted">Removes everything this account holds from the server.</p>
      <button
        type="button"
        className="button danger-text"
        onClick={() => {
          setOpen(true);
        }}
      >
        Delete account…
      </button>
      {open ? (
        <DeleteDialog
          user={user}
          onClose={() => {
            setOpen(false);
          }}
        />
      ) : null}
    </section>
  );
}

export function AccountPage() {
  useDocumentTitle("Account");
  const { user } = useAuth();
  if (user === null) {
    // The signed-in frame sends a signed-out visitor to sign-in first.
    return null;
  }
  return (
    <section>
      <h1>Account</h1>
      <EmailPanel user={user} />
      <SessionsPanel />
      <QuotaPanel />
      <DisclosurePanel />
      <ExportPanel />
      <DeletePanel user={user} />
    </section>
  );
}
