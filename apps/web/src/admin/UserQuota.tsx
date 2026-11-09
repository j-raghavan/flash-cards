// SPDX-License-Identifier: AGPL-3.0-only
// One user's monthly AI page limit (F-ADMIN-3): an override of the instance
// default, or no override. The server validates and audits every change.

import { useEffect, useRef, useState, type SubmitEvent } from "react";

import { adminUsage, parsePages, periodLabel, type UserQuota } from "../api/adminUsage";
import type { AdminUser } from "../api/types";
import { ErrorText, Loading } from "../components/ui";
import { useResource } from "../lib/useResource";
import { useSubmit } from "../lib/useSubmit";
import { PAGES_ERROR } from "./QuotasPanel";

function pagesText(n: number): string {
  return `${n.toLocaleString()} ${n === 1 ? "page" : "pages"}`;
}

function QuotaForm({
  account,
  quota,
  onSaved,
  onClose,
}: {
  account: AdminUser;
  quota: UserQuota;
  onSaved: (quota: UserQuota) => void;
  onClose: () => void;
}) {
  const [text, setText] = useState(quota.override === null ? "" : String(quota.override));
  const [checked, setChecked] = useState(false);
  // A limit below this month's use, waiting for confirmation.
  const [pending, setPending] = useState<number | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const submit = useSubmit();
  const inputRef = useRef<HTMLInputElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const base = `quota-${account.id}`;

  const empty = text.trim() === "";
  const pages = empty ? null : parsePages(text);
  const error = checked && !empty && pages === null ? PAGES_ERROR : null;

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  useEffect(() => {
    if (pending !== null) {
      cancelRef.current?.focus();
    }
  }, [pending]);

  const save = (value: number | null): void => {
    setPending(null);
    setNote(null);
    void submit.run(async () => {
      const saved = await adminUsage.setUserQuota(account.id, value);
      onSaved(saved);
      setText(saved.override === null ? "" : String(saved.override));
      setChecked(false);
      setNote(
        saved.override === null
          ? `Override cleared: the instance default of ${pagesText(saved.monthly_pages)} applies.`
          : `Saved: ${pagesText(saved.monthly_pages)} a month.`,
      );
      inputRef.current?.focus();
    });
  };

  const onSubmit = (event: SubmitEvent): void => {
    event.preventDefault();
    setChecked(true);
    if (!empty && pages === null) {
      inputRef.current?.focus();
      return;
    }
    // A limit below what they have already used stops their generation at once.
    if (pages !== null && pages < quota.used_pages && pages !== quota.override) {
      setPending(pages);
      return;
    }
    save(pages);
  };

  return (
    <form className="stack" onSubmit={onSubmit} aria-label={`AI quota for ${account.email}`} noValidate>
      <p id={`${base}-use`}>
        {pagesText(quota.used_pages)} used of {pagesText(quota.monthly_pages)} in {periodLabel(quota.period)}.{" "}
        {quota.override === null ? "The instance default applies." : "This is an override of the instance default."}
      </p>
      <label className="field">
        <span>Monthly AI pages for {account.email} (pages)</span>
        <input
          ref={inputRef}
          type="number"
          inputMode="numeric"
          min={0}
          step={1}
          value={text}
          aria-invalid={error !== null}
          aria-describedby={error === null ? `${base}-use ${base}-hint` : `${base}-use ${base}-hint ${base}-error`}
          onChange={(event) => {
            setText(event.target.value);
          }}
        />
      </label>
      <p className="muted" id={`${base}-hint`}>
        Leave empty to use the instance default.
      </p>
      {error === null ? null : (
        <p className="error" id={`${base}-error`}>
          {error}
        </p>
      )}
      <div className="row">
        <button type="submit" className="button primary" disabled={submit.busy}>
          Save quota
        </button>
        {quota.override === null ? null : (
          <button
            type="button"
            className="button"
            disabled={submit.busy}
            onClick={() => {
              save(null);
            }}
          >
            Use the default
          </button>
        )}
        <button type="button" className="button" onClick={onClose}>
          Close
        </button>
        <span role="status" className="muted">
          {note}
        </span>
      </div>
      {pending === null ? null : (
        <div className="confirm" role="alertdialog" aria-label="Confirm the quota">
          <p>
            {account.email} has already used {pagesText(quota.used_pages)} this month. A limit of {pagesText(pending)}{" "}
            stops new AI generation for them until next month.
          </p>
          <div className="row">
            <button
              type="button"
              className="button danger"
              onClick={() => {
                save(pending);
              }}
            >
              Set the limit
            </button>
            <button
              type="button"
              className="button"
              ref={cancelRef}
              onClick={() => {
                setPending(null);
                inputRef.current?.focus();
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}
      <ErrorText message={submit.error} />
    </form>
  );
}

export function UserQuotaEditor({ account, onClose }: { account: AdminUser; onClose: () => void }) {
  const quota = useResource(() => adminUsage.userQuota(account.id), `quota:${account.id}`);
  if (quota.error !== null) {
    return (
      <div className="row">
        <ErrorText message={quota.error} />
        <button type="button" className="button" onClick={onClose}>
          Close
        </button>
      </div>
    );
  }
  if (quota.data === undefined) {
    return <Loading />;
  }
  return (
    <QuotaForm
      account={account}
      quota={quota.data}
      onClose={onClose}
      onSaved={(saved) => {
        quota.setData(saved);
      }}
    />
  );
}
