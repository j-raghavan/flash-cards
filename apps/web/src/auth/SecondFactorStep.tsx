// SPDX-License-Identifier: AGPL-3.0-only
// The second step of sign-in (F-AUTH-3): an authenticator code, a recovery code
// or a passkey finishes the sign-in the password started. The pending sign-in
// lives five minutes on the server and works once.

import { startAuthentication } from "@simplewebauthn/browser";
import { useId, useState, type SubmitEvent } from "react";

import { ApiError } from "../api/client";
import { secondStep, type SecondFactorRequired } from "../api/twoFactor";
import type { User } from "../api/types";
import { ErrorText } from "../components/ui";
import { useSubmit } from "../lib/useSubmit";

/** A WebAuthn ceremony the person cancelled or the browser refused, said plainly. */
export function passkeyError(error: unknown): Error {
  if (error instanceof Error && (error.name === "NotAllowedError" || error.name === "AbortError")) {
    return new Error("The passkey request was cancelled or timed out. Try again.", { cause: error });
  }
  return error instanceof Error ? error : new Error("The passkey could not be used.");
}

export function SecondFactorStep({
  pending,
  onSignedIn,
  onRestart,
}: {
  pending: SecondFactorRequired;
  onSignedIn: (user: User) => void;
  onRestart: (message: string | null) => void;
}) {
  const [code, setCode] = useState("");
  const submit = useSubmit();
  const codeId = useId();
  const hasTotp = pending.methods.includes("totp");
  const hasPasskey = pending.methods.includes("passkey");
  const hasRecovery = pending.methods.includes("recovery_code");

  const finish = async (attempt: () => Promise<User>): Promise<void> => {
    try {
      onSignedIn(await attempt());
    } catch (failure) {
      // An expired or used sign-in cannot be retried here: start again.
      if (failure instanceof ApiError && failure.status === 401 && failure.message.includes("expired")) {
        onRestart(failure.message);
        return;
      }
      throw failure;
    }
  };

  const onSubmit = (event: SubmitEvent): void => {
    event.preventDefault();
    void submit.run(() => finish(() => secondStep.withCode(pending.token, code.trim())));
  };

  const usePasskey = (): void => {
    void submit.run(() =>
      finish(async () => {
        const { options } = await secondStep.passkeyOptions(pending.token);
        const credential = await startAuthentication({ optionsJSON: options }).catch((failure: unknown) => {
          throw passkeyError(failure);
        });
        return secondStep.withPasskey(pending.token, credential);
      }),
    );
  };

  let codeLabel = "Recovery code";
  if (hasTotp) {
    codeLabel = hasRecovery
      ? "Code from your authenticator app, or a recovery code"
      : "Code from your authenticator app";
  }

  return (
    <div className="stack">
      <p>Your account has two-step sign-in. Confirm it is you to finish signing in.</p>
      {hasPasskey ? (
        <button type="button" className="button primary" disabled={submit.busy} onClick={usePasskey}>
          Use a passkey
        </button>
      ) : null}
      {hasTotp || hasRecovery ? (
        <form onSubmit={onSubmit} className="stack">
          <div className="field">
            <label htmlFor={codeId}>{codeLabel}</label>
            <input
              id={codeId}
              required
              autoComplete="one-time-code"
              autoCapitalize="off"
              spellCheck={false}
              maxLength={64}
              value={code}
              onChange={(event) => {
                setCode(event.target.value);
              }}
            />
          </div>
          <button type="submit" className={hasPasskey ? "button" : "button primary"} disabled={submit.busy}>
            {submit.busy ? "Checking…" : "Verify"}
          </button>
        </form>
      ) : null}
      <ErrorText message={submit.error} />
      <button
        type="button"
        className="button"
        disabled={submit.busy}
        onClick={() => {
          onRestart(null);
        }}
      >
        Start over
      </button>
    </div>
  );
}
