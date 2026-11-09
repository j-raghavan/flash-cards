# Self-hosting with Docker Compose

This runbook covers running Flash-Cards on a single host with Docker Compose: first boot, the first admin account, upgrades, backups and restore, and the security settings that matter. The hosted free-tier path is in [deploy-phase0.md](deploy-phase0.md); the threat model is in [docs/security/threat-model.md](../security/threat-model.md).

## What the stack contains

Everything lives in `deploy/compose/`:

| File | Purpose |
|---|---|
| `compose.yaml` | The stack definition |
| `.env.example` | Template for `.env`, documenting every setting in `apps/api/src/flashcards/config.py` |
| `Caddyfile` | Configuration for the optional HTTPS proxy |

The web service's own configuration is `apps/web/Caddyfile`, baked into its image by `apps/web/Dockerfile`.

| Service | Role |
|---|---|
| `db` | Postgres 16 with the named volume `pgdata` and a `pg_isready` healthcheck. No host port is published. |
| `migrate` | One-shot job running `alembic upgrade head`. It starts once `db` is healthy and must exit successfully before `api` starts. |
| `api` | The API image built from `apps/api/Dockerfile`, running as a non-root user, with a `/healthz` healthcheck. No host port is published; it is reached through `web`. |
| `web` | The web app and the site's entry point: a Caddy image (`apps/web/Dockerfile`) that serves the built app and forwards `/api/*` to `api`. It is published on `127.0.0.1:8000` only, over plain HTTP, and runs unprivileged with a read-only root file system. |
| `proxy` | Optional (profile `proxy`). Caddy, which obtains and renews a TLS certificate for `FLASHCARDS_DOMAIN` and forwards everything to `web`. |

`db` and `migrate` sit on a `backend` network, which is internal (no route to the internet). `api` joins both `backend` and `frontend`; `web` and `proxy` join only `frontend`, so neither reaches Postgres.

All persistent data, including uploaded media and AI source documents, is stored in Postgres. The `pgdata` volume is the only state that needs backing up; the `caddy_data` volume holds certificates, which Caddy can obtain again.

## First boot

Requirements: Docker Engine with the Compose v2 plugin (the stack is tested with Compose 2.31). If the Caddy proxy is used, a DNS name pointing at the host, with ports 80 and 443 reachable.

1. Create the environment file:

   ```sh
   cd deploy/compose
   cp .env.example .env
   chmod 600 .env
   ```

2. Edit `.env`. The minimum is:

   - `POSTGRES_PASSWORD`: a long random value using URL-safe characters (`openssl rand -hex 32`). It is embedded in the database URL.
   - `FLASHCARDS_PUBLIC_BASE_URL`: the public HTTPS origin of the site, for example `https://flashcards.example.org`, without `/api`. Email links point at the web app's pages under it (`/verify-email#token=...`, `/reset-password#token=...`), and the API logs a warning at startup when it still contains `localhost`.
   - `FLASHCARDS_DOMAIN`, if the Caddy proxy is used: the host name from the URL above.
   - An AI provider and SMTP server, if wanted (see the comments in `.env.example`).

   Leave optional settings commented out instead of setting them to empty values.

3. Start the stack:

   ```sh
   # Site on 127.0.0.1:8000, behind a reverse proxy already running on the host:
   docker compose up -d --build --wait

   # Or with the bundled Caddy proxy on ports 80 and 443:
   docker compose --profile proxy up -d --build --wait
   ```

4. Check it:

   ```sh
   docker compose ps -a                         # migrate "Exited (0)"; db, api and web "healthy"
   curl -s http://127.0.0.1:8000/api/healthz    # {"status":"ok","version":"..."}
   curl -sI http://127.0.0.1:8000/              # 200, the web app, with a Content-Security-Policy header
   ```

### The first admin account

There is no public sign-up, so the first account comes from an invite minted with the management CLI (`apps/api/src/flashcards/cli.py`). It needs `FLASHCARDS_DATABASE_URL`, which the `api` container already has:

```sh
docker compose exec api flashcards create-invite --role admin
# Optionally bind it to one address:
docker compose exec api flashcards create-invite --role admin --email admin@example.org
```

The command prints one invite token, valid for 7 days and usable once. Only its SHA-256 hash is stored, so a lost token cannot be recovered; mint another. Redeem it in the web app by opening the sign-up page with the token in the URL fragment:

```
https://flashcards.example.org/signup#token=<token>
```

Or call the API directly with `POST /api/auth/signup` and a JSON body of `token`, `email` and `password` (at least 12 characters):

```sh
curl -si https://flashcards.example.org/api/auth/signup \
  -H 'Content-Type: application/json' \
  -d '{"token":"<token>","email":"admin@example.org","password":"<password>"}'
```

The response is `201` with a session cookie. From then on the admin endpoints (`/api/admin/invites`, `/api/admin/users`, `/api/admin/audit`) mint invites and manage accounts. The token is printed to the terminal, so treat the terminal scrollback and any captured output as sensitive until the invite has been redeemed.

## The web app and routing

The site is one origin, because the API sends no CORS headers and its session cookie is `SameSite=Lax`. The `web` service (`apps/web/Caddyfile`) routes requests as follows:

| Path | Handling |
|---|---|
| `/api/*` | Forwarded to `api` with the `/api` prefix stripped (`/api/decks` reaches `/decks`). Responses are flushed as they arrive, so the Server-Sent Events of `/api/ai/jobs/{id}/events` are not buffered. |
| `/api` | `404`, never the app. |
| `/assets/*` | Vite's content-hashed build output, `Cache-Control: public, max-age=31536000, immutable`. A missing file is a `404`. |
| anything else | The file if it exists (`index.html`, `sw.js`, `manifest.webmanifest`, icons), otherwise `index.html` for client-side routes such as `/decks/...` or `/verify-email`. Sent with `Cache-Control: no-cache`, so browsers revalidate and pick up a new release and service worker immediately. |

Every response carries the app's Content-Security-Policy (including `frame-ancestors 'none'`, which only works as a header), `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`, `Cross-Origin-Opener-Policy: same-origin` and a restrictive `Permissions-Policy`. `Strict-Transport-Security` is added when the request reached the TLS proxy over HTTPS (`X-Forwarded-Proto: https` from a proxy on a private network). The policy must match `CONTENT_SECURITY_POLICY` in `apps/web/vite.config.ts`; `apps/web/deploy.test.ts` fails if they differ.

To run only the web image against an API elsewhere, set `FLASHCARDS_API_UPSTREAM` (default `api:8000`) on the container.

### Local development

The stack is not needed for development. Run the API and the Vite dev server side by side; Vite proxies `/api` to the API and strips the prefix, just as the `web` service does:

```sh
# Terminal 1: the API on http://localhost:8000 (in-memory unless FLASHCARDS_DATABASE_URL is set)
cd apps/api
FLASHCARDS_ENVIRONMENT=development FLASHCARDS_DEV_BOOTSTRAP_INVITE=true \
  FLASHCARDS_SESSION_COOKIE_SECURE=false .venv/bin/uvicorn flashcards.api.app:app --reload

# Terminal 2: the web app on http://localhost:5173
cd apps/web
npm ci
npm run dev
```

In this in-memory development mode the API logs an admin invite token at startup (`DEV BOOTSTRAP ... admin invite token = ...`); open `http://localhost:5173/signup#token=<token>` to create an account. Set `FLASHCARDS_API_PROXY_TARGET` to point Vite at an API on another address. `FLASHCARDS_PUBLIC_BASE_URL` defaults to `http://localhost:5173`, so email links printed in development open the dev server.

## Upgrades

```sh
git pull
cd deploy/compose
docker compose build
docker compose up -d --wait          # add --profile proxy if used
```

`migrate` runs again on every `up` and applies any new migrations before the new `api` container starts. If it fails, the old `api` container is not replaced; `docker compose logs migrate` shows why. Take a backup before upgrading across releases that add migrations (`apps/api/migrations/versions/`). Migrations are forward-only in practice, so the backup is the rollback path.

To update the base images (Postgres minor releases, Caddy):

```sh
docker compose pull db proxy
docker compose up -d --wait
```

Postgres major upgrades (16 to 17) need a dump and restore into a fresh volume; changing the image tag alone does not work.

## Backups

Back up the database with `pg_dump` in the custom format. It is consistent while the API keeps running:

```sh
docker compose exec -T db pg_dump -U flashcards -d flashcards -Fc > "flashcards-$(date +%F).dump"
```

Backups contain everything the instance holds: email addresses, Argon2 password hashes, hashed session and invite tokens, decks, uploaded documents and media, and the audit log. Store them encrypted and with restricted access. A backup still holds the data of accounts deleted after it was taken, so expire backups on a fixed schedule that matches the instance's deletion promise to its users.

Restoring into a fresh stack is the only reliable test of a backup. Do one after the first backup and periodically afterwards.

## Restore

```sh
docker compose stop api
docker compose exec -T db dropdb -U flashcards flashcards
docker compose exec -T db createdb -U flashcards flashcards
docker compose exec -T db pg_restore -U flashcards -d flashcards --no-owner < flashcards-YYYY-MM-DD.dump
docker compose up -d --wait
```

`migrate` runs on `up` and brings a dump taken on an older release up to the current schema. Every session in the dump is restored with it; to force everyone to sign in again, revoke sessions after the restore (for example `UPDATE sessions SET revoked_at = now() WHERE revoked_at IS NULL;` in `psql`).

## Security notes

- **HTTPS is required.** The session cookie is `Secure` (`FLASHCARDS_SESSION_COOKIE_SECURE=true`, the default), so browsers send it only over HTTPS. Serve the instance through a TLS-terminating reverse proxy: the bundled Caddy profile, or an existing proxy on the host forwarding to `127.0.0.1:8000`. Set `FLASHCARDS_SESSION_COOKIE_SECURE=false` only for plain-HTTP testing on a private machine.
- **Keep the site port off the public network.** The `web` port is published on loopback only, and the API publishes none. `web` keeps `X-Forwarded-*` headers from proxies on private addresses, and the API trusts them from `web` (`FORWARDED_ALLOW_IPS=*`), on that basis. Do not change the port binding to `0.0.0.0` or publish the API port.
- **`FLASHCARDS_PUBLIC_BASE_URL`** must be the public HTTPS origin. The API's cross-site request check accepts state-changing requests whose `Origin` is this origin or matches the request's `Host`, so every proxy in front must pass `Origin` and `Host` through unchanged (both bundled Caddy configurations do; the nginx example below sets `Host $host`). Email links carry their single-use token in the URL fragment, which browsers do not send to servers, so tokens stay out of proxy access logs.
- **`FLASHCARDS_ENVIRONMENT=production`** (the image default) disables `/docs` and `/openapi.json` and keeps email bodies, which contain working reset links, out of the logs. Never run a reachable instance in `development`.
- **Request size.** The API reads `.apkg` and AI uploads into memory before checking their size. The bundled Caddyfile caps request bodies at 50 MB; set an equivalent limit on any other proxy (`client_max_body_size 50m;` in nginx).
- **Server-Sent Events.** `GET /api/ai/jobs/{id}/events` streams job progress. The API sends `X-Accel-Buffering: no`, which nginx honours; both bundled Caddy configurations use `flush_interval -1`, and `web` does not compress `/api` responses. Other proxies must not buffer `text/event-stream` responses, and their read timeout must exceed the 15-second keep-alive interval (streams last at most 10 minutes).
- **Rate limiting.** Sign-in lockout is per email address (5 failures per 15 minutes). There is no per-IP limit in the API; add one at the proxy for `/api/auth/*` if the instance is reachable from the internet.
- **Security headers.** `web` sends the Content-Security-Policy and the other headers listed under [The web app and routing](#the-web-app-and-routing) on every response, and HSTS for requests that arrived over HTTPS. A proxy in front should pass them through rather than add its own copies.
- **Share links in logs.** Share-link tokens appear in request paths (`/api/shared/{token}`), so the API's access log (`docker compose logs api`) contains working links, as does any proxy access log that is enabled (neither bundled Caddyfile enables one). Restrict who can read those logs.
- **Unhealthy containers are not restarted.** `restart: unless-stopped` restarts a container whose process exits. Compose does not restart a container that is running but failing its healthcheck; use external monitoring on `/api/healthz` (from the host, `http://127.0.0.1:8000/api/healthz`).
- **Secrets.** `.env` holds every secret (database password, AI key, SMTP password). Keep it `chmod 600` and out of version control (`.env` is git-ignored, and `.dockerignore` keeps it out of the image build context). The `api` and `migrate` containers receive the whole file as environment variables, including `POSTGRES_PASSWORD`, which they need for the database URL anyway.

## nginx example

For hosts that already run nginx in front of `127.0.0.1:8000`. The security headers come from `web` (HSTS because of `X-Forwarded-Proto: https`), so nginx does not add them:

```nginx
server {
    listen 443 ssl;
    http2 on;
    server_name flashcards.example.org;
    # ssl_certificate and ssl_certificate_key as usual

    client_max_body_size 50m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;   # not the client's own header
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;   # above the SSE keep-alive interval
    }
}
```

## Troubleshooting

- **`migrate` exits non-zero:** `docker compose logs migrate`. A wrong `POSTGRES_PASSWORD` after the volume was first created is the usual cause: Postgres keeps the password from the first boot. Either restore the original value or change the role's password in `psql`.
- **Sign-in succeeds but the next request is `401`:** the cookie is `Secure` and the site is being reached over plain HTTP.
- **Emails not arriving:** without `FLASHCARDS_SMTP_HOST` no mail is sent, and the API logs `did not send: <subject>`. Send failures are logged as `could not send email`.
- **AI features fail:** no provider is configured, or it is unreachable. Synchronous AI requests (grading, summaries, mock exams, chat) return `503`; generation jobs end in the `failed` state with an error on the job. The startup log says when no provider is configured.
