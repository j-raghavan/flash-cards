# Self-hosting with Docker Compose

This runbook covers running Flash-Cards on a single host with Docker Compose: first boot, the first admin account, upgrades, backups and restore, and the security settings that matter. The hosted free-tier path is in [deploy-phase0.md](deploy-phase0.md); the threat model is in [docs/security/threat-model.md](../security/threat-model.md).

## What the stack contains

Everything lives in `deploy/compose/`:

| File | Purpose |
|---|---|
| `compose.yaml` | The stack definition |
| `.env.example` | Template for `.env`, documenting every setting in `apps/api/src/flashcards/config.py` |
| `Caddyfile` | Configuration for the optional HTTPS proxy |
| `upgrade.sh` | First start and upgrades: backup, build, migrate, wait for health |
| `backup.sh` | Verified `pg_dump` backups with retention, run by the `backup` service |
| `restore.sh` | Restores the database from one of those backups |

The web service's own configuration is `apps/web/Caddyfile`, baked into its image by `apps/web/Dockerfile`.

| Service | Role |
|---|---|
| `db` | Postgres 16 with a `pg_isready` healthcheck. Its data is in the named volume `pgdata`, or in a host directory when `FLASHCARDS_PGDATA` is set. No host port is published. |
| `backup` | Takes a verified database backup every `FLASHCARDS_BACKUP_INTERVAL_HOURS` (default 24), keeping the newest `FLASHCARDS_BACKUP_KEEP` (default 14) in `FLASHCARDS_BACKUP_DIR` (default `deploy/compose/backups`). Its healthcheck turns unhealthy when no backup was written for two intervals. |
| `migrate` | One-shot job running `alembic upgrade head`. It starts once `db` is healthy and must exit successfully before `api` starts. |
| `api` | The API image built from `apps/api/Dockerfile`, running as a non-root user, with a `/healthz` healthcheck. No host port is published; it is reached through `web`. |
| `web` | The web app and the site's entry point: a Caddy image (`apps/web/Dockerfile`) that serves the built app and forwards `/api/*` to `api`. It is published on `127.0.0.1:8000` only, over plain HTTP, and runs unprivileged with a read-only root file system. |
| `proxy` | Optional (profile `proxy`). Caddy, which obtains and renews a TLS certificate for `FLASHCARDS_DOMAIN` and forwards everything to `web`. |

`db`, `backup` and `migrate` sit on a `backend` network, which is internal (no route to the internet). `api` joins both `backend` and `frontend`; `web` and `proxy` join only `frontend`, so neither reaches Postgres.

All persistent data, including uploaded media and AI source documents, is stored in Postgres, so the database is the only state that needs backing up; the `caddy_data` volume holds certificates, which Caddy can obtain again. The `backup` service writes database backups to `FLASHCARDS_BACKUP_DIR`. Copy that directory and `.env` off the host (see [Backups](#backups)).

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
   - `FLASHCARDS_TOTP_ENCRYPTION_KEYS`, to offer authenticator apps for two-factor sign-in: generate a key with `openssl rand -base64 32 | tr "+/" "-_"` (or, once the stack runs, `docker compose exec api flashcards generate-totp-key`). Passkeys work without it, and their relying party is the host of `FLASHCARDS_PUBLIC_BASE_URL`, so changing that host invalidates registered passkeys.

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

### Admin decks are shared with every member

Every deck owned by an active admin is a library deck: every member sees it on their home page under Library and can study it with their own progress, but cannot change, export or share it. There is no per-deck opt-in. Keep private material in a separate non-admin account.

- Making a user an admin, or unsuspending an admin, publishes every deck they own at once, including decks made while they were a member. The admin console asks before either change.
- Demoting, suspending or deleting an admin takes their decks out of the library at once. Members' progress on those cards stays until the cards are deleted.
- Deleting a library deck, note or card, or the admin's account, erases every member's progress on those cards.

See ADR 0024 for the design.

### Instances without email

Without `FLASHCARDS_SMTP_HOST` no email is sent, so invites and password resets cannot reach anyone. An admin can instead add accounts directly under Admin → Users → Add an account (`POST /api/admin/users`), and set a member's password there when they forget it. The account is ready at once; share the password in person. See ADR 0023 for the limits.

### Privacy policy, terms and imprint

The web app shows `/privacy`, `/terms` and `/imprint` to everyone and links them from every page and from sign-up. Their text is the operator's to write: until it is provided, each page shows a template headed by a notice that the operator has not filled it in, with every part to complete marked *Operator: ...*. The templates are not legal advice.

1. Write the three pages as Markdown files named exactly `privacy.md`, `terms.md` and `imprint.md` (UTF-8, at most 200 KB each), for example in `deploy/compose/legal/`. Start from the templates: `curl -s http://127.0.0.1:8000/api/legal/privacy | jq -r .markdown > legal/privacy.md`, then replace the notice and every *Operator* part.
2. Mount the directory into the `api` service read-only and point the API at it. In a `compose.override.yaml` next to `compose.yaml`:

   ```yaml
   services:
     api:
       volumes:
         - ./legal:/etc/flashcards/legal:ro
   ```

   and in `.env`: `FLASHCARDS_LEGAL_DIR=/etc/flashcards/legal`.
3. Restart the API (`docker compose up -d api`). The pages are read once at startup, so every edit needs a restart.
4. Check: `curl -s http://127.0.0.1:8000/api/legal/imprint | jq .is_template` prints `false` for a page you provided.

A missing file falls back to its template, with a warning in the API log. A file that is there but unusable (too large, not UTF-8, empty) or a `FLASHCARDS_LEGAL_DIR` that is not a directory stops the API at startup with the reason in its log, so a mistake shows up on deploy. The Markdown is rendered through the same sanitizer as card text: plain formatting and links work, while scripts, forms, inline styles and images from other sites are removed.

### YouTube transcripts

Generating a deck from a YouTube link is off by default: the link answers that no transcript is available, and the user pastes the transcript instead. With `FLASHCARDS_YOUTUBE_TRANSCRIPTS_ENABLED=true` the API fetches the video's captions from youtube.com itself (the `api` container reaches the internet through the `frontend` network). Only YouTube's own hosts are contacted, each request is time-limited and size-capped, and at most two fetches run at once (ADR 0020). `FLASHCARDS_YOUTUBE_TRANSCRIPT_LANGUAGES` (default `en`, for example `de,en`) sets the preferred caption languages.

YouTube refuses many data-centre IP ranges and changes its pages from time to time, so retrieval is best-effort; refused fetches are logged as `no YouTube transcript for <id>: <reason>` and the user is offered the paste box. Keep the image current so the transcript library keeps up with YouTube.

### Home network over plain HTTP

For an instance used only inside one home or classroom network, the site can be served over plain HTTP to the local network, with nothing installed on the devices that use it:

```sh
FLASHCARDS_HTTP_BIND=0.0.0.0
FLASHCARDS_HTTP_PORT=8000
FLASHCARDS_SESSION_COOKIE_SECURE=false
FLASHCARDS_PUBLIC_BASE_URL=http://<host address>:8000
```

People then open `http://<host address>:8000`. Without HTTPS, browsers turn off some features:

- **Offline:** offline study and installing the app to the home screen are unavailable (service workers need HTTPS).
- **Passkeys:** passkeys for two-step sign-in do not work; authenticator apps still do.
- **Traffic:** passwords and cards cross the network unencrypted. Use it only on a network you trust, and never forward the port from the internet.

Moving to HTTPS later (a reverse proxy with a certificate, or Tailscale) changes only these settings; the data is unaffected.

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
./deploy/compose/upgrade.sh
```

`upgrade.sh` does the whole upgrade, and the first start too:

1. If the database is running, it takes a verified backup and stops if the backup fails.
2. It builds the images from the current source.
3. It starts the stack. `migrate` applies any new migrations before the `api` container starts. If a migration fails, the API stays down and the script prints the `migrate` and `api` logs.
4. It waits until the API reports healthy (`WAIT_SECONDS`, default 300).

Migrations are forward-only in practice, so the backup taken in step 1 is the rollback path: check out the previous release and restore it (see [Restore](#restore)). Set `COMPOSE` (and `DOCKER`) when the commands differ, for example `COMPOSE="sudo docker-compose" DOCKER="sudo docker"`. With the `proxy` profile, set `COMPOSE_PROFILES=proxy` in `.env` so every command includes it.

To update the base images (Postgres minor releases, Caddy):

```sh
docker compose pull db proxy
docker compose up -d --wait
```

Postgres major upgrades (16 to 17) need a backup and a restore into a fresh, empty data volume or directory; changing the image tag alone does not work. Change the `db` and `backup` images together: `pg_dump` must match the server's major version.

## Backups

The `backup` service backs the database up automatically every `FLASHCARDS_BACKUP_INTERVAL_HOURS` (default 24). When it starts, it takes one only if none is newer than the interval, so restarts, reboots and restores do not rotate good backups out. Each backup is a `pg_dump` in the custom format, consistent while the API keeps running. It is written under a temporary name, read back in full (`pg_restore` to `/dev/null`), and only then renamed to `flashcards-YYYYMMDD-HHMMSS.dump`, so a failed or truncated dump never replaces a good one. The newest `FLASHCARDS_BACKUP_KEEP` (default 14) are kept and older ones deleted. Files are readable by their owner only. `docker compose logs backup` shows each run, and `docker compose ps` shows the service as unhealthy when no backup was written for two intervals.

To take one now:

```sh
docker compose run --rm --no-deps -T backup once
```

Backups on the same disk do not survive the loss of that disk. Copy `FLASHCARDS_BACKUP_DIR` and `.env` to another machine or to off-site storage on a schedule (for example with the host's backup tool). Keep `.env` with them: it holds `POSTGRES_PASSWORD` and `FLASHCARDS_TOTP_ENCRYPTION_KEYS`.

Backups contain everything the instance holds: email addresses, Argon2 password hashes, hashed session and invite tokens, decks, uploaded documents and media, and the audit log. Store them encrypted and with restricted access. A backup still holds the data of accounts deleted after it was taken, so expire backups on a fixed schedule that matches the instance's deletion promise to its users.

Restoring into a fresh stack is the only reliable test of a backup. Do one after the first backup and periodically afterwards.

`FLASHCARDS_PGDATA` chooses where the database lives. Changing it on an existing install points Postgres at a different, empty directory, and the site starts empty (the old data stays where it was). To move the data, take a backup, change the setting, start the stack, and restore the backup with `restore.sh`.

## Restore

```sh
./deploy/compose/restore.sh --yes deploy/compose/backups/flashcards-YYYYMMDD-HHMMSS.dump
```

The script replaces the whole database with the backup:

1. It stops `api`, `web` and `backup`, so nothing writes meanwhile.
2. It saves the current database as `pre-restore-YYYYMMDD-HHMMSS.dump` in the backup directory, and stops if that fails. Retention never deletes these files; remove them by hand once they are no longer needed. `--no-safety-backup` skips this step, for example when the database cannot be read at all.
3. It drops and recreates the database, so nothing from a newer schema is left behind, and restores the backup in a single transaction: a failure leaves an empty database, never a half-restored one.
4. It starts the stack again.

It works on a fresh, empty database too, for example on a new host or after the data directory was lost. Without `--yes` it does nothing. To roll back an upgrade, check out the release the backup was taken with, then restore it. When recovering from damage, copy the backup directory aside first, so nothing in it can change while you try backups one after another.

`migrate` runs on `up` and brings a dump taken on an older release up to the current schema. Every session in the dump is restored with it; to force everyone to sign in again, revoke sessions after the restore (for example `UPDATE sessions SET revoked_at = now() WHERE revoked_at IS NULL;` in `psql`).

## Security notes

- **HTTPS is required.** The session cookie is `Secure` (`FLASHCARDS_SESSION_COOKIE_SECURE=true`, the default), so browsers send it only over HTTPS. Serve the instance through a TLS-terminating reverse proxy: the bundled Caddy profile, or an existing proxy on the host forwarding to `127.0.0.1:8000`. Set `FLASHCARDS_SESSION_COOKIE_SECURE=false` only for plain-HTTP testing on a private machine.
- **Keep the site port off the public network.** The `web` port is published on loopback only, and the API publishes none. `web` keeps `X-Forwarded-*` headers from proxies on private addresses, and the API trusts them from `web` (`FORWARDED_ALLOW_IPS=*`), on that basis. Do not change the port binding to `0.0.0.0` or publish the API port.
- **`FLASHCARDS_PUBLIC_BASE_URL`** must be the public HTTPS origin. The API's cross-site request check accepts state-changing requests whose `Origin` is this origin or matches the request's `Host`, so every proxy in front must pass `Origin` and `Host` through unchanged (both bundled Caddy configurations do; the nginx example below sets `Host $host`). Email links carry their single-use token in the URL fragment, which browsers do not send to servers, so tokens stay out of proxy access logs.
- **`FLASHCARDS_ENVIRONMENT=production`** (the image default) disables `/docs` and `/openapi.json` and keeps email bodies, which contain working reset links, out of the logs. Never run a reachable instance in `development`.
- **Request size.** The API reads `.apkg` and AI uploads into memory before checking their size. The bundled Caddyfile caps request bodies at 50 MB; set an equivalent limit on any other proxy (`client_max_body_size 50m;` in nginx).
- **Server-Sent Events.** `GET /api/ai/jobs/{id}/events` streams job progress. The API sends `X-Accel-Buffering: no`, which nginx honours; both bundled Caddy configurations use `flush_interval -1`, and `web` does not compress `/api` responses. Other proxies must not buffer `text/event-stream` responses, and their read timeout must exceed the 15-second keep-alive interval (streams last at most 10 minutes).
- **Rate limiting.** Sign-in lockout is per email address (5 failures per 15 minutes). The API also limits each client address: 20 POST requests a minute under `/auth/` and 60 requests a minute under `/shared/`, answering 429 with `Retry-After` (ADR 0019; `FLASHCARDS_RATE_LIMIT_AUTH_PER_MINUTE`, `FLASHCARDS_RATE_LIMIT_SHARED_PER_MINUTE`, `FLASHCARDS_RATE_LIMIT_ENABLED`). The counts live in the API process's memory, so each process limits on its own and a restart resets them; for a global limit, also limit at the proxy (see [Rate limiting at the proxy](#rate-limiting-at-the-proxy)). The client address is what uvicorn reports, which in this stack comes from `X-Forwarded-For` because of `FORWARDED_ALLOW_IPS`. When uvicorn does not rewrite it (the API runs behind proxies it is not told to trust), set `FLASHCARDS_TRUSTED_PROXY_COUNT` to the number of proxies that append to `X-Forwarded-For`; leave it at 0 otherwise, or clients can choose their own address. Any proxy in front of this stack must overwrite `X-Forwarded-For` with the connecting address (`proxy_set_header X-Forwarded-For $remote_addr;` in nginx, as below), not append to it (`$proxy_add_x_forwarded_for`): the stack trusts every hop, so an appended header would let a client pick its own rate-limit bucket or exhaust someone else's.
- **Security headers.** `web` sends the Content-Security-Policy and the other headers listed under [The web app and routing](#the-web-app-and-routing) on every response, and HSTS for requests that arrived over HTTPS. A proxy in front should pass them through rather than add its own copies.
- **Share links in logs.** Share-link tokens appear in request paths (`/api/shared/{token}`), so the API's access log (`docker compose logs api`) contains working links, as does any proxy access log that is enabled (neither bundled Caddyfile enables one). Restrict who can read those logs.
- **Unhealthy containers are not restarted.** `restart: unless-stopped` restarts a container whose process exits. Compose does not restart a container that is running but failing its healthcheck; use external monitoring on `/api/healthz` (from the host, `http://127.0.0.1:8000/api/healthz`).
- **Two-factor keys.** `FLASHCARDS_TOTP_ENCRYPTION_KEYS` encrypts authenticator-app secrets, so the database and its backups alone cannot generate codes. Back the key up apart from the database: without it no stored authenticator app can be used, and users fall back to recovery codes, passkeys or an admin reset. Rotate it as described in `.env.example` (ADR 0018).
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

## Rate limiting at the proxy

The API's own limit is per process. A proxy limit covers every process and stops a burst before it reaches the API. With nginx:

```nginx
# In the http block: 20 requests a minute per client address, for sign-in and sharing.
limit_req_zone $binary_remote_addr zone=flashcards_auth:10m rate=20r/m;

# In the server block, next to `location /`:
location ~ ^/api/(auth|shared)/ {
    limit_req zone=flashcards_auth burst=20 nodelay;
    limit_req_status 429;
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

Caddy has no rate limiter built in. With a Caddy build that includes the `caddy-ratelimit` module (`xcaddy build --with github.com/mholt/caddy-ratelimit`), add to the site block of `deploy/compose/Caddyfile`:

```caddyfile
rate_limit {
	zone flashcards_auth {
		match {
			path /api/auth/* /api/shared/*
		}
		key {remote_host}
		events 20
		window 1m
	}
}
```

and `order rate_limit before reverse_proxy` in the global options block.

## Troubleshooting

- **`migrate` exits non-zero:** `docker compose logs migrate`. A wrong `POSTGRES_PASSWORD` after the volume was first created is the usual cause: Postgres keeps the password from the first boot. Either restore the original value or change the role's password in `psql`.
- **The API restarts in a loop with exit code 132 and no log output on an Apple Silicon Mac:** OpenSSL's CPU feature detection hits an unsupported instruction inside Docker's Linux VM on recent Apple chips. Add `OPENSSL_armcap=0` to `.env`. Intel and AMD hosts are unaffected.
- **Sign-in succeeds but the next request is `401`:** the cookie is `Secure` and the site is being reached over plain HTTP.
- **Emails not arriving:** without `FLASHCARDS_SMTP_HOST` no mail is sent, and the API logs `did not send: <subject>`. Send failures are logged as `could not send email`.
- **AI features fail:** no provider is configured, or it is unreachable. Synchronous AI requests (grading, summaries, mock exams, chat) return `503`; generation jobs end in the `failed` state with an error on the job. The startup log says when no provider is configured.
