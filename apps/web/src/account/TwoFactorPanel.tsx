// SPDX-License-Identifier: AGPL-3.0-only
// Two-step sign-in on the account page (F-AUTH-3): set up or turn off an
// authenticator app, add and remove passkeys, and get recovery codes. Changes
// that add or remove a factor ask for the password or a current code.

import { startRegistration } from "@simplewebauthn/browser";
import { useId, useState, type ReactNode, type SubmitEvent } from "react";

import {
  recoveryCodesText,
  twoFactor,
  type Passkey,
  type Proof,
  type TotpSetup,
  type TwoFactorStatus,
} from "../api/twoFactor";
import { passkeyError } from "../auth/SecondFactorStep";
import { CopyField, ErrorText, Loading } from "../components/ui";
import { useResource } from "../lib/useResource";
import { useSubmit } from "../lib/useSubmit";
import { QrCode } from "./QrCode";

const DOWNLOAD_URL_LIFETIME_MS = 60_000;

function formatDay(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { dateStyle: "medium" });
}

/** Passwords are at least 12 characters, so six digits can only be a code. */
export function proofFrom(value: string): Proof {
  const trimmed = value.trim();
  return /^\d{6}$/.test(trimmed) ? { code: trimmed } : { password: value };
}

function saveText(text: string, fileName: string): void {
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => {
    URL.revokeObjectURL(url);
  }, DOWNLOAD_URL_LIFETIME_MS);
}

/** Asks for the password (or a code) before a change, then runs it. */
function ProofForm({
  submitLabel,
  withCode,
  onProof,
  onCancel,
  children,
}: {
  submitLabel: string;
  withCode: boolean;
  onProof: (proof: Proof) => Promise<void>;
  onCancel: () => void;
  children?: ReactNode;
}) {
  const [value, setValue] = useState("");
  const submit = useSubmit();
  const inputId = useId();
  const onSubmit = (event: SubmitEvent): void => {
    event.preventDefault();
    void submit.run(() => onProof(proofFrom(value)));
  };
  return (
    <form onSubmit={onSubmit} className="stack">
      {children}
      <div className="field">
        <label htmlFor={inputId}>
          {withCode ? "Your password, or a code from your authenticator app" : "Your password"}
        </label>
        <input
          id={inputId}
          type="password"
          required
          autoComplete="current-password"
          value={value}
          onChange={(event) => {
            setValue(event.target.value);
          }}
        />
      </div>
      <ErrorText message={submit.error} />
      <div className="row">
        <button type="submit" className="button primary" disabled={submit.busy}>
          {submitLabel}
        </button>
        <button type="button" className="button" disabled={submit.busy} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function RecoveryCodesView({ codes, email, onDone }: { codes: string[]; email: string; onDone: () => void }) {
  return (
    <div className="stack" role="region" aria-label="Your recovery codes">
      <p>
        <strong>Save these recovery codes now.</strong> Each one signs you in once if you lose your authenticator
        app or passkey. They are shown only this time.
      </p>
      <ol className="recovery-codes">
        {codes.map((code) => (
          <li key={code}>
            <code>{code}</code>
          </li>
        ))}
      </ol>
      <div className="row">
        <button
          type="button"
          className="button"
          onClick={() => {
            saveText(recoveryCodesText(codes, email), "flash-cards-recovery-codes.txt");
          }}
        >
          Download codes
        </button>
        <button type="button" className="button primary" onClick={onDone}>
          I have saved them
        </button>
      </div>
    </div>
  );
}

function TotpSetupView({
  setup,
  onConfirmed,
  onCancel,
}: {
  setup: TotpSetup;
  onConfirmed: (codes: string[] | null) => void;
  onCancel: () => void;
}) {
  const [code, setCode] = useState("");
  const submit = useSubmit();
  const codeId = useId();
  const onSubmit = (event: SubmitEvent): void => {
    event.preventDefault();
    void submit.run(async () => {
      const result = await twoFactor.confirmTotp(code.trim());
      onConfirmed(result.recovery_codes);
    });
  };
  return (
    <form onSubmit={onSubmit} className="stack">
      <p>Scan this code with your authenticator app, or enter the key by hand.</p>
      <QrCode value={setup.uri} label="QR code for your authenticator app" />
      <CopyField label="Setup key" value={setup.secret} />
      <div className="field">
        <label htmlFor={codeId}>The 6-digit code your app shows</label>
        <input
          id={codeId}
          required
          inputMode="numeric"
          autoComplete="one-time-code"
          maxLength={10}
          value={code}
          onChange={(event) => {
            setCode(event.target.value);
          }}
        />
      </div>
      <ErrorText message={submit.error} />
      <div className="row">
        <button type="submit" className="button primary" disabled={submit.busy}>
          Turn on
        </button>
        <button type="button" className="button" disabled={submit.busy} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

type Mode =
  | { kind: "idle" }
  | { kind: "totp-proof" }
  | { kind: "totp-setup"; setup: TotpSetup }
  | { kind: "totp-off" }
  | { kind: "passkey-add" }
  | { kind: "passkey-remove"; passkey: Passkey }
  | { kind: "codes-proof" }
  | { kind: "codes"; codes: string[] };

function Panel({ status, email, reload }: { status: TwoFactorStatus; email: string; reload: () => void }) {
  const [mode, setMode] = useState<Mode>({ kind: "idle" });
  const [note, setNote] = useState<string | null>(null);
  const [name, setName] = useState("");
  const nameId = useId();
  const idle = (): void => {
    setMode({ kind: "idle" });
  };
  const done = (message: string, codes: string[] | null = null): void => {
    setNote(message);
    setMode(codes === null ? { kind: "idle" } : { kind: "codes", codes });
    reload();
  };

  if (mode.kind === "codes") {
    return <RecoveryCodesView codes={mode.codes} email={email} onDone={idle} />;
  }
  if (mode.kind === "totp-setup") {
    return (
      <TotpSetupView
        setup={mode.setup}
        onCancel={idle}
        onConfirmed={(codes) => {
          done("Your authenticator app is on.", codes);
        }}
      />
    );
  }

  const start = (next: Mode): void => {
    setNote(null);
    setMode(next);
  };

  return (
    <div className="stack">
      <p>
        {status.enabled
          ? "Two-step sign-in is on: after your password, you confirm it is you with your authenticator app, a passkey or a recovery code."
          : "Two-step sign-in is off. Add an authenticator app or a passkey so a stolen password is not enough to sign in."}
      </p>

      <h3>Authenticator app</h3>
      {status.totp ? (
        <p>On.</p>
      ) : status.totp_available ? (
        <p className="muted">Not set up.</p>
      ) : (
        <p className="muted">Authenticator apps are not available on this server.</p>
      )}
      {mode.kind === "totp-proof" ? (
        <ProofForm
          submitLabel="Continue"
          withCode={false}
          onCancel={idle}
          onProof={async (proof) => {
            setMode({ kind: "totp-setup", setup: await twoFactor.beginTotp(proof) });
          }}
        />
      ) : null}
      {mode.kind === "totp-off" ? (
        <ProofForm
          submitLabel="Turn off"
          withCode
          onCancel={idle}
          onProof={async (proof) => {
            await twoFactor.disableTotp(proof);
            done("Your authenticator app is off.");
          }}
        />
      ) : null}
      {mode.kind === "idle" && status.totp ? (
        <button
          type="button"
          className="button"
          onClick={() => {
            start({ kind: "totp-off" });
          }}
        >
          Turn off authenticator app
        </button>
      ) : null}
      {mode.kind === "idle" && !status.totp && status.totp_available ? (
        <button
          type="button"
          className="button"
          onClick={() => {
            start({ kind: "totp-proof" });
          }}
        >
          Set up authenticator app
        </button>
      ) : null}

      <h3>Passkeys</h3>
      {status.passkeys.length === 0 ? <p className="muted">No passkeys.</p> : null}
      <ul className="plain-list">
        {status.passkeys.map((passkey) => (
          <li key={passkey.id}>
            <strong>{passkey.name}</strong>
            <br />
            <span className="muted">
              Added {formatDay(passkey.created_at)}.
              {passkey.last_used_at === null ? " Not used yet." : ` Last used ${formatDay(passkey.last_used_at)}.`}
            </span>
            {mode.kind === "passkey-remove" && mode.passkey.id === passkey.id ? (
              <ProofForm
                submitLabel={`Remove ${passkey.name}`}
                withCode={status.totp}
                onCancel={idle}
                onProof={async (proof) => {
                  await twoFactor.removePasskey(passkey.id, proof);
                  done(`Removed ${passkey.name}.`);
                }}
              />
            ) : (
              <div>
                <button
                  type="button"
                  className="button danger-text"
                  disabled={mode.kind !== "idle"}
                  aria-label={`Remove passkey ${passkey.name}`}
                  onClick={() => {
                    start({ kind: "passkey-remove", passkey });
                  }}
                >
                  Remove
                </button>
              </div>
            )}
          </li>
        ))}
      </ul>
      {mode.kind === "passkey-add" ? (
        <ProofForm
          submitLabel="Add passkey"
          withCode={status.totp}
          onCancel={idle}
          onProof={async (proof) => {
            const registration = await twoFactor.beginPasskey(proof);
            const credential = await startRegistration({ optionsJSON: registration.options }).catch(
              (failure: unknown) => {
                throw passkeyError(failure);
              },
            );
            const added = await twoFactor.finishPasskey(registration.token, credential, name.trim());
            setName("");
            done(`Added ${added.passkey.name}.`, added.recovery_codes);
          }}
        >
          <div className="field">
            <label htmlFor={nameId}>Name (for example, the device it is on)</label>
            <input
              id={nameId}
              maxLength={60}
              value={name}
              onChange={(event) => {
                setName(event.target.value);
              }}
            />
          </div>
        </ProofForm>
      ) : null}
      {mode.kind === "idle" ? (
        <button
          type="button"
          className="button"
          onClick={() => {
            start({ kind: "passkey-add" });
          }}
        >
          Add a passkey
        </button>
      ) : null}

      {status.enabled ? (
        <>
          <h3>Recovery codes</h3>
          <p className="muted">
            {status.recovery_codes_left === 1
              ? "1 unused recovery code left."
              : `${String(status.recovery_codes_left)} unused recovery codes left.`}
          </p>
          {mode.kind === "codes-proof" ? (
            <ProofForm
              submitLabel="Get new codes"
              withCode={status.totp}
              onCancel={idle}
              onProof={async (proof) => {
                const result = await twoFactor.newRecoveryCodes(proof);
                done("New recovery codes are ready; the old ones no longer work.", result.recovery_codes);
              }}
            />
          ) : null}
          {mode.kind === "idle" ? (
            <button
              type="button"
              className="button"
              onClick={() => {
                start({ kind: "codes-proof" });
              }}
            >
              Get new recovery codes
            </button>
          ) : null}
        </>
      ) : null}
      <p role="status" className="muted">
        {note}
      </p>
    </div>
  );
}

export function TwoFactorPanel({ email }: { email: string }) {
  const status = useResource(twoFactor.status, "two-factor");
  return (
    <section className="panel" aria-labelledby="two-factor-heading">
      <h2 id="two-factor-heading">Two-step sign-in</h2>
      {status.data === undefined && status.error === null ? <Loading /> : null}
      <ErrorText message={status.error} />
      {status.data === undefined ? null : <Panel status={status.data} email={email} reload={status.reload} />}
    </section>
  );
}
