// SPDX-License-Identifier: AGPL-3.0-only
// Two-factor sign-in (F-AUTH-3, ADR 0018): the second step of sign-in and the
// account's authenticator app, passkeys and recovery codes. Shapes mirror
// apps/api/src/flashcards/api/two_factor_schemas.py.

import type {
  AuthenticationResponseJSON,
  PublicKeyCredentialCreationOptionsJSON,
  PublicKeyCredentialRequestOptionsJSON,
  RegistrationResponseJSON,
} from "@simplewebauthn/browser";

import { request } from "./client";
import type { AdminUser, User } from "./types";

export type SecondFactorMethod = "totp" | "passkey" | "recovery_code";

/** POST /auth/login answers this (202) when the account has a second factor. */
export interface SecondFactorRequired {
  second_factor_required: true;
  token: string;
  methods: SecondFactorMethod[];
}

export function needsSecondFactor(result: User | SecondFactorRequired): result is SecondFactorRequired {
  return "second_factor_required" in result;
}

/** The password, or a current authenticator code, confirming a change. */
export interface Proof {
  password?: string;
  code?: string;
}

export interface Passkey {
  id: string;
  name: string;
  created_at: string;
  last_used_at: string | null;
}

export interface TwoFactorStatus {
  enabled: boolean;
  totp: boolean;
  totp_available: boolean;
  passkeys: Passkey[];
  recovery_codes_left: number;
}

export interface TotpSetup {
  secret: string;
  uri: string;
}

export interface RecoveryCodes {
  recovery_codes: string[] | null;
}

export interface PasskeyRegistration {
  token: string;
  options: PublicKeyCredentialCreationOptionsJSON;
}

export interface PasskeyAdded {
  passkey: Passkey;
  recovery_codes: string[] | null;
}

const id = encodeURIComponent;

export const secondStep = {
  withCode: (token: string, code: string) =>
    request<User>("/auth/login/second-factor", { method: "POST", json: { token, code } }),
  passkeyOptions: (token: string) =>
    request<{ options: PublicKeyCredentialRequestOptionsJSON }>("/auth/login/passkey/options", {
      method: "POST",
      json: { token },
    }),
  withPasskey: (token: string, credential: AuthenticationResponseJSON) =>
    request<User>("/auth/login/passkey", { method: "POST", json: { token, credential } }),
};

export const twoFactor = {
  status: () => request<TwoFactorStatus>("/auth/two-factor"),
  beginTotp: (proof: Proof) => request<TotpSetup>("/auth/two-factor/totp", { method: "POST", json: proof }),
  confirmTotp: (code: string) =>
    request<RecoveryCodes>("/auth/two-factor/totp/confirm", { method: "POST", json: { code } }),
  disableTotp: (proof: Proof) => request<undefined>("/auth/two-factor/totp", { method: "DELETE", json: proof }),
  newRecoveryCodes: (proof: Proof) =>
    request<RecoveryCodes>("/auth/two-factor/recovery-codes", { method: "POST", json: proof }),
  beginPasskey: (proof: Proof) =>
    request<PasskeyRegistration>("/auth/two-factor/passkeys/options", { method: "POST", json: proof }),
  finishPasskey: (token: string, credential: RegistrationResponseJSON, name: string) =>
    request<PasskeyAdded>("/auth/two-factor/passkeys", { method: "POST", json: { token, credential, name } }),
  removePasskey: (passkeyId: string, proof: Proof) =>
    request<undefined>(`/auth/two-factor/passkeys/${id(passkeyId)}`, { method: "DELETE", json: proof }),
  adminReset: (userId: string) => request<AdminUser>(`/admin/users/${id(userId)}/two-factor/reset`, { method: "POST" }),
};

/** The recovery codes as a text file to keep. */
export function recoveryCodesText(codes: string[], email: string): string {
  return [
    `Flash-Cards recovery codes for ${email}`,
    "Each code signs you in once, in place of your authenticator app or passkey.",
    "",
    ...codes,
    "",
  ].join("\n");
}
