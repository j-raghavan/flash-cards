# ADR 0018: Two-factor sign-in with TOTP and passkeys

- **Status:** Accepted
- **Date:** 2026-11-08

## Context

F-AUTH-3 asks for optional passkeys (WebAuthn) and TOTP two-factor sign-in. The threat model lists "no second factor" as a residual risk for password guessing and for admin takeover (risks 5.1, 5.7 and 6). Sign-in today is one step: `POST /auth/login` checks the password under the per-email lockout (ADR 0010, F-AUTH-6) and sets the session cookie. In SQL mode a request is one transaction, so anything that must survive a refused request (a failed-attempt count) has to be returned as a response, not raised.

## Decision

### Model

- Two-factor sign-in is **on** for an account when it has a confirmed authenticator app (TOTP) or at least one passkey. It is optional per account; nothing is forced on admins yet.
- The second factor can be any factor the account has: an authenticator code, a passkey, or a recovery code.
- Tables (migration 0017, each with `ON DELETE CASCADE` from `users`): `totp_credentials` (one per user), `passkeys`, `recovery_codes`, `two_factor_challenges`. Account deletion also deletes them explicitly, so the in-memory adapter matches.

### Passkeys are a second factor, not a password replacement

A passkey answers the second step after the password; it does not replace the password. Reasons:

- One sign-in model. The password step keeps its lockout, its enumeration-safe answers and its timing equalisation unchanged, and every second factor goes through the same pending-sign-in state.
- Passwordless sign-in needs a usernameless flow with discoverable credentials, its own throttling (there is no email to count against) and account-recovery rules for accounts with no password in use. That is a larger change to the threat model.
- Registration asks for non-discoverable credentials (`residentKey: discouraged`) and prefers, but does not require, user verification: the password is the knowledge factor, the passkey proves possession.

Passwordless passkey sign-in is left for later; the stored credentials (public key, counter, transports) already support it.

### The second step

1. `POST /auth/login` with the right password, for an account with two-factor sign-in on, answers **202** `{"second_factor_required": true, "token": "...", "methods": [...]}` and sets no cookie. The token is 32 random bytes; only its SHA-256 hash is stored, in a `sign_in` challenge that lives **5 minutes** and is used **once**.
2. The client finishes with `POST /auth/login/second-factor {token, code}` (an authenticator code or a recovery code), or with `POST /auth/login/passkey/options {token}` followed by `POST /auth/login/passkey {token, credential}`.
3. Only a successful answer issues the session. The challenge is marked used with one conditional `UPDATE ... WHERE used_at IS NULL`, so two right answers racing each other produce one session. The account's suspension is checked again.

The token travels in the response body and comes back in the request body, not in a cookie. A cookie could be planted by a sibling subdomain (session fixation of the pending state); a body token is bound to the response the client itself received. The CSRF middleware (ADR 0017) covers both endpoints like any other unsafe request.

**Lockout.** Every second-step answer, right or wrong, is counted against the same per-email lockout as the password, before it is checked. A right password with a second factor pending does **not** clear the count; only a completed sign-in does. So the password and the codes share 5 attempts per 15 minutes, a stolen password opens at most four code guesses per lock period, and the failure that starts a lock is audited (`login.locked`). Wrong codes are returned (401), not raised, so the count is committed.

Second-factor attempts are also counted under a per-account key (`second-factor:<user id>`, hashed) with the same limits. A password reset clears the per-email count, as before, but not this one; only a completed sign-in or an admin unlock clears it. Otherwise whoever controls the mailbox could reset the password every minute (the reset cooldown) to get four fresh code guesses each time.

### TOTP

- `pyotp`, RFC 6238 defaults that authenticator apps expect: 6 digits, 30-second steps, SHA-1, a 160-bit random secret.
- Codes from one step either side of now are accepted (clock drift). All three candidates are compared with `hmac.compare_digest`, whichever matches.
- **Replay.** The credential stores the last accepted time step. A code is accepted only for a later step, recorded with one conditional `UPDATE ... WHERE last_used_step < :step`. A code therefore works once, and an older code cannot be used after a newer one.
- **Enrolment.** `POST /auth/two-factor/totp` (with the password) stores a new, unconfirmed secret and returns it once with its `otpauth://` URI; the web client draws the QR code itself, so the secret never leaves the browser for a third-party service. `POST /auth/two-factor/totp/confirm` turns it on with the first valid code, within 15 minutes. Confirmation codes count against the lockout too, so a stolen session cannot guess the code of a setup the owner left pending (and receive the new recovery codes). Starting again replaces an unconfirmed secret.

### Encryption at rest for TOTP secrets

A TOTP secret is a shared secret: anyone who reads it can generate codes, so a database dump or backup alone would defeat the second factor. Secrets are encrypted with Fernet (AES-128-CBC with HMAC-SHA256, from `cryptography`) under keys from `FLASHCARDS_TOTP_ENCRYPTION_KEYS`.

- **Generation:** `flashcards generate-totp-key` prints a new key (32 random bytes, url-safe base64). Keep it with the other secrets in `.env`, not in the database or its backups.
- **Rotation:** the setting is a comma-separated list, newest first. The first key encrypts; every key decrypts. To rotate, put a new key in front, restart, run `flashcards rotate-totp-key` (re-encrypts every stored secret with the first key; safe while the API serves), then remove the old key.
- **Without a key**, a SQL-backed instance offers no authenticator apps (`totp_available: false`) and logs a warning at startup; passkeys and recovery codes still work. A secret that cannot be decrypted (a lost key) never verifies; the user signs in with a recovery code or a passkey, or an admin resets the second factor. In-memory mode, where nothing outlives the process, uses a throwaway key.

Passkeys store only public keys, so they need no encryption.

### Recovery codes

- 10 codes, issued when two-factor sign-in turns on (the first factor confirmed) and when the user asks for new ones (which replaces all old ones). Shown once; the web client offers them as a text download.
- Each code is 16 characters from a 32-letter alphabet without look-alike characters, 80 random bits, shown as four groups of four. With 80 bits a fast hash cannot be reversed offline, so codes are stored as SHA-256 hashes (like invite and session tokens) rather than with Argon2, which would cost 64 MiB per check. Input is compared without spaces, hyphens or case.
- Each works once (conditional `UPDATE`). Using one is audited with the number left. Turning the last factor off deletes them.

### Passkeys (WebAuthn)

- `py_webauthn` on the server, `@simplewebauthn/browser` on the client.
- The relying party id is the host of `FLASHCARDS_PUBLIC_BASE_URL` and the only accepted origin is its origin (scheme, host, non-default port). A response signed for any other origin or RP id is refused, so a phishing page on another domain cannot use a passkey. A misconfigured URL does not stop the API: passkeys then use an unusable RP id and simply fail.
- Registration (`POST /auth/two-factor/passkeys/options`, with the password, then `POST /auth/two-factor/passkeys`) keeps the WebAuthn challenge server-side in a `passkey_registration` challenge bound to the user, used once. Attestation is not requested (`none`); the same credential cannot be registered twice; at most 10 passkeys per account.
- Sign-in: the options call stores a fresh WebAuthn challenge on the pending sign-in; the first answer, right or wrong, clears it with a compare-and-set, so of concurrent answers to one challenge only one is checked.
- A registration whose response fails verification is answered 422 without rolling back, so its token stays used. The passkey must belong to the pending user.
- **Counters.** After the signature verifies, the authenticator's counter must be greater than the stored one, unless both are zero (authenticators that do not count). A counter that does not move forward is refused and audited as `two_factor.passkey_counter_regressed` (a possible clone). The new count is stored with a compare-and-set on the old one.

### Changing second factors needs re-authentication

Starting an authenticator or passkey enrolment, removing either, and issuing new recovery codes require the password or a current authenticator code in addition to the session (`{"password": ...}` or `{"code": ...}`). A stolen session alone therefore cannot add an attacker's factor, which would lock the owner out, or strip the owner's. These checks count against the same lockout, so a stolen session cannot be used to guess the password without limit, and a wrong proof is returned as 403 (not 401, which the client treats as "signed out").

### Administration and audit

`POST /admin/users/{id}/two-factor/reset` deletes a user's authenticator, passkeys, recovery codes and challenges, for a user who lost their device; the user then signs in with the password alone. The admin user list reports `two_factor` per user. New audit actions: `two_factor.totp_enabled`, `two_factor.totp_disabled`, `two_factor.passkey_added`, `two_factor.passkey_removed`, `two_factor.passkey_counter_regressed`, `two_factor.recovery_codes_generated`, `two_factor.recovery_code_used`, `two_factor.reset` (with the admin as actor). Failed codes are not audited one by one, as for passwords; the lock they start is.

## Consequences

- Sign-in for an account with two-factor sign-in on takes two requests, and a client must handle the 202 answer. Accounts without it are unchanged.
- Codes and passwords share the lockout budget: a user who mistypes the password twice has three code attempts left in that window.
- Accepting one step of drift means a code is valid for up to 90 seconds; replay protection limits each to one use.
- Losing `FLASHCARDS_TOTP_ENCRYPTION_KEYS` disables every authenticator app on the instance until users fall back to recovery codes or passkeys, or an admin resets them. The key must be backed up separately from the database.
- An admin can remove any user's second factor, so a compromised admin account can still take over member accounts (threat model 5.7). Requiring two-factor sign-in for admins, and passwordless passkey sign-in, are follow-ups.
- The password alone is accepted as proof for adding or removing a factor, also when two-factor sign-in is on, so a stolen session plus a stolen password can replace the owner's factors. Requiring a current second factor for those changes once one exists, and emailing the owner about every factor change and admin reset, are follow-ups.
- When the attempt that starts a lock is a right password for an account with a second factor, the answer is still 202, and the second step is then refused with 429 until the lock ends.
