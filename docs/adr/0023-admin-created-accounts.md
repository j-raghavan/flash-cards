# ADR 0023: Admin-created accounts and admin-set passwords

- **Status:** Accepted
- **Date:** 2026-11-09

## Context

Invites and password resets both rely on email (ADR 0010, ADR 0016). An instance with no outgoing mail, such as a server for one household or a classroom, has no way to bring someone in or let them back in except by handing out invite links and asking them to sign up. The operator of such an instance wants to add people directly and to set a forgotten password.

ADR 0010 states that there is no impersonation: an admin can manage accounts but never act as another user. Setting someone's password touches that rule, so the limits are recorded here.

## Decision

### Creating an account

- `POST /admin/users` takes an email, a password (at least 12 characters) and a role, and creates the account at once. No invite is created and no email is sent.
- The email counts as verified, since the admin vouches for it.
- Duplicates answer 409, and bad input answers 422 without echoing the password.
- The change takes the admin locks like every other user change, and is audited as `user.registered` with `via=admin` and the role.

### Setting a password

- `POST /admin/users/{id}/password` sets a member's password.
- **Never an admin's.** An admin's password cannot be set by another admin (409); demote them first. That demotion is itself audited and visible. Admins cannot use it on their own account either; they use the account page.
- **Effects, as after a reset:**
  - the member is signed out everywhere
  - every emailed link (reset, verification) stops working
  - the sign-in lockouts are cleared, as an admin unlock does
- **Two-factor sign-in stays on.** For a member who uses it, the new password is only the first step.
- **The member is told.** When mail is configured, they get an email saying an administrator set their password.
- **Auditing.** It is recorded as `user.password_set`, with the admin as actor and `lockouts_cleared` in the detail.
- **Hashing comes first.** Both operations hash the password before taking any lock, so slow Argon2 work never holds the admin rows.

## Consequences

- An admin can sign in as a member by setting their password, unless the member uses two-factor sign-in. This is a deliberate, limited exception to "no impersonation":
  - it is audited
  - it signs the member out, so they notice at their next visit
  - it emails them when mail is on
  - it can never be used against another admin
- Together with the admin two-factor reset (ADR 0018), an admin can take over a member account that uses two-factor sign-in. That was already possible through the reset; this ADR adds the password step but no new class of access.
- A two-factor sign-in challenge started with the old password just before the change can still be completed for its few minutes of validity. The same is true after a password reset, so both flows can be tightened together later.
- There is no "must change password at next sign-in" flag yet. Members are asked to change it in the notice email, and on mail-less instances the admin tells them in person.
