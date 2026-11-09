// SPDX-License-Identifier: AGPL-3.0-only
// Instance AI quotas (F-ADMIN-3): the default monthly page limit per user and
// the monthly spend cap, with this month's spend against the cap. The server
// validates and audits every change; the checks here only mirror its bounds.

import { useEffect, useRef, useState, type SubmitEvent } from "react";

import {
  MAX_MONTHLY_PAGES,
  MAX_SPEND_CAP_MICRO_USD,
  adminUsage,
  formatUsd,
  parsePages,
  parseUsd,
  usdInput,
  type InstanceQuotas,
  type UsageReport,
} from "../api/adminUsage";
import { ErrorText, Loading } from "../components/ui";
import { useResource } from "../lib/useResource";
import { useSubmit } from "../lib/useSubmit";

export const PAGES_ERROR = `Enter a whole number of pages from 0 to ${MAX_MONTHLY_PAGES.toLocaleString()}.`;
export const CAP_ERROR = `Enter an amount in US dollars up to ${formatUsd(MAX_SPEND_CAP_MICRO_USD)}, such as 25 or 12.50, or leave it empty for no cap.`;

/** The cap from its input: null for no cap, undefined when the text is not a valid amount. */
function parseCap(text: string): number | null | undefined {
  if (text.trim() === "") {
    return null;
  }
  const micro = parseUsd(text);
  return micro === null || micro > MAX_SPEND_CAP_MICRO_USD ? undefined : micro;
}

function SpendThisMonth({ report, cap }: { report: UsageReport; cap: number | null }) {
  const spent = report.cost_micro_usd;
  const tokens = report.by_provider.reduce((sum, p) => sum + p.input_tokens + p.output_tokens, 0);
  return (
    <div className="panel">
      <h2>This month</h2>
      {cap === null ? (
        <p>{formatUsd(spent)} spent. There is no spend cap, so AI work is limited only by page quotas.</p>
      ) : (
        <>
          <p id="spend-against-cap">
            {formatUsd(spent)} spent of the {formatUsd(cap)} cap.
          </p>
          <progress value={Math.min(spent, cap)} max={Math.max(cap, 1)} aria-labelledby="spend-against-cap" />
          {spent >= cap ? (
            <p>
              <strong>The cap is reached:</strong> new AI work is refused for everyone until next month.
            </p>
          ) : null}
        </>
      )}
      {tokens > 0 && spent === 0 ? (
        <p className="muted">Prices are not configured, so costs show as $0.00 and the spend cap has no effect.</p>
      ) : null}
    </div>
  );
}

interface Pending {
  quotas: InstanceQuotas;
  cap: number;
  spent: number;
}

function QuotasForm({
  saved,
  spent,
  onSaved,
}: {
  saved: InstanceQuotas;
  spent: number | undefined;
  onSaved: (quotas: InstanceQuotas) => void;
}) {
  const [pages, setPages] = useState(String(saved.default_monthly_pages));
  const [cap, setCap] = useState(
    saved.monthly_spend_cap_micro_usd === null ? "" : usdInput(saved.monthly_spend_cap_micro_usd),
  );
  const [checked, setChecked] = useState(false);
  // Values waiting for the admin to confirm a cap below this month's spend.
  const [pending, setPending] = useState<Pending | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const submit = useSubmit();
  const pagesRef = useRef<HTMLInputElement>(null);
  const capRef = useRef<HTMLInputElement>(null);
  const saveRef = useRef<HTMLButtonElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);

  const pageCount = parsePages(pages);
  const capValue = parseCap(cap);
  const pagesError = checked && pageCount === null ? PAGES_ERROR : null;
  const capError = checked && capValue === undefined ? CAP_ERROR : null;

  useEffect(() => {
    if (pending !== null) {
      cancelRef.current?.focus();
    }
  }, [pending]);

  const save = (quotas: InstanceQuotas): void => {
    setPending(null);
    void submit.run(async () => {
      onSaved(await adminUsage.setQuotas(quotas));
      setNote("Saved.");
    });
  };

  const onSubmit = (event: SubmitEvent): void => {
    event.preventDefault();
    setChecked(true);
    setNote(null);
    if (pageCount === null) {
      pagesRef.current?.focus();
      return;
    }
    if (capValue === undefined) {
      capRef.current?.focus();
      return;
    }
    const quotas = { default_monthly_pages: pageCount, monthly_spend_cap_micro_usd: capValue };
    // A new cap below what this month has already spent stops all AI work at once.
    if (capValue !== null && capValue !== saved.monthly_spend_cap_micro_usd && spent !== undefined && capValue < spent) {
      setPending({ quotas, cap: capValue, spent });
      return;
    }
    save(quotas);
  };

  return (
    <form className="stack" onSubmit={onSubmit} aria-label="AI quotas" noValidate>
      <label className="field">
        <span>Default monthly limit per user (pages)</span>
        <input
          ref={pagesRef}
          type="number"
          inputMode="numeric"
          min={0}
          max={MAX_MONTHLY_PAGES}
          step={1}
          value={pages}
          aria-invalid={pagesError !== null}
          aria-describedby={pagesError === null ? "quota-pages-hint" : "quota-pages-hint quota-pages-error"}
          onChange={(event) => {
            setPages(event.target.value);
          }}
        />
      </label>
      <p className="muted" id="quota-pages-hint">
        AI pages each user may generate per calendar month (UTC), unless their account has an override. 0 allows none.
      </p>
      {pagesError === null ? null : (
        <p className="error" id="quota-pages-error">
          {pagesError}
        </p>
      )}
      <label className="field">
        <span>Monthly spend cap (US dollars)</span>
        <input
          ref={capRef}
          type="text"
          inputMode="decimal"
          autoComplete="off"
          value={cap}
          aria-invalid={capError !== null}
          aria-describedby={capError === null ? "quota-cap-hint" : "quota-cap-hint quota-cap-error"}
          onChange={(event) => {
            setCap(event.target.value);
          }}
        />
      </label>
      <p className="muted" id="quota-cap-hint">
        Estimated cost of all AI work on this instance per calendar month. Once reached, new AI work is refused until the
        next month. Leave empty for no cap.
      </p>
      {capError === null ? null : (
        <p className="error" id="quota-cap-error">
          {capError}
        </p>
      )}
      <div className="row">
        <button type="submit" className="button primary" disabled={submit.busy} ref={saveRef}>
          Save quotas
        </button>
        <span role="status" className="muted">
          {note}
        </span>
      </div>
      {pending === null ? null : (
        <div className="confirm" role="alertdialog" aria-label="Confirm the spend cap">
          <p>
            This month has already spent {formatUsd(pending.spent)}. A cap of {formatUsd(pending.cap)} refuses all new
            AI work for everyone until next month.
          </p>
          <div className="row">
            <button
              type="button"
              className="button danger"
              onClick={() => {
                save(pending.quotas);
                saveRef.current?.focus();
              }}
            >
              Set the cap
            </button>
            <button
              type="button"
              className="button"
              ref={cancelRef}
              onClick={() => {
                setPending(null);
                capRef.current?.focus();
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

export function QuotasPanel() {
  const quotas = useResource(() => adminUsage.quotas(), "admin:quotas");
  const spend = useResource(() => adminUsage.usage(null), "usage:current");

  if (quotas.error !== null) {
    return <ErrorText message={quotas.error} />;
  }
  if (quotas.data === undefined) {
    return <Loading />;
  }
  return (
    <>
      <QuotasForm
        saved={quotas.data}
        spent={spend.data?.cost_micro_usd}
        onSaved={(saved) => {
          quotas.setData(saved);
        }}
      />
      <ErrorText message={spend.error} />
      {spend.data === undefined ? null : (
        <SpendThisMonth report={spend.data} cap={quotas.data.monthly_spend_cap_micro_usd} />
      )}
    </>
  );
}
