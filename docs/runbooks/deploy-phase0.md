# Deploying Phase 0 (Render free + Supabase)

This is how I bring up the invite-only instance on the free tier. It follows the phased plan in [ADR 0004](../adr/0004-phased-hosting.md) and the persistence design in [ADR 0005](../adr/0005-async-persistence-and-unit-of-work.md).

## 1. Create the database

I make a Postgres database on Supabase Free and copy its connection string. A plain `postgresql://user:pass@host:5432/db` is fine; the app coerces it to the asyncpg driver itself.

## 2. Deploy the service

The repo ships a Render Blueprint (`render.yaml`): one Docker web service on the free plan, health-checked at `/healthz`. I point Render at the repo and set one secret in the dashboard:

- `FLASHCARDS_DATABASE_URL` = the Supabase connection string.

`FLASHCARDS_ENVIRONMENT=production` and `FLASHCARDS_INVITE_ONLY=true` are already in the blueprint.

On every boot the container runs `alembic upgrade head` before starting the server (only when `FLASHCARDS_DATABASE_URL` is set), so the schema is created and kept current without a separate release step.

## 3. Mint the first admin invite

There is no public sign-up, so the first account comes from a one-off command. In a Render shell for the service:

```
flashcards create-invite --role admin
```

It prints a single invite token. I use it once at `POST /auth/signup` (or the sign-up screen) with my email and password to create the admin account. From there the admin console mints every other invite.

## 4. Verify

- `GET /healthz` returns `{"status":"ok",...}`.
- Signing up with the token returns 201 and sets a session cookie.
- `GET /auth/me` returns the admin account.

## Notes

- Free services sleep after idle; the first request after a sleep is slow. That is expected on Phase 0.
- The session cookie is `Secure` in production, so the instance must be served over HTTPS (Render does this by default).
- To move off the free tier, see the Phase 1 section of ADR 0004. Nothing in the application code changes; it is configuration and hosting.
