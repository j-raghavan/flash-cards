# Self-hosting on a Synology NAS

This runbook runs the [compose stack](self-hosting-compose.md) on a Synology NAS, reached privately over [Tailscale](https://tailscale.com) with HTTPS. Read the compose runbook first; this one covers only what differs on DSM.

It was written for a DS918+ (Intel Celeron J3455, DSM 7.2.1, Container Manager with Docker 24 and Compose 2.20). Any x86_64 model that runs Container Manager on DSM 7.2 or later works the same way.

## Layout

Everything lives in one shared folder, so Hyper Backup can protect it as a unit:

| Path | Contents |
|---|---|
| `/volume1/docker/flash-cards/src` | The source tree (a copy of the repository) |
| `/volume1/docker/flash-cards/src/deploy/compose/.env` | Settings and secrets (`chmod 600`) |
| `/volume1/docker/flash-cards/pgdata` | Postgres data (`FLASHCARDS_PGDATA`) |
| `/volume1/docker/flash-cards/backups` | Verified nightly database backups (`FLASHCARDS_BACKUP_DIR`) |

`/volume1/docker` is the shared folder Container Manager creates.

## One-time setup in DSM

1. **Container Manager:** Package Center → Container Manager → Install.
2. **SSH:** Control Panel → Terminal & SNMP → Enable SSH service.
3. **Home folders:** Control Panel → User & Group → Advanced → Enable user home service. SSH keys live in the home folder.
4. **SSH key:** from your computer, `ssh-copy-id <user>@<nas>`. DSM creates the files group- and world-writable, which lets other accounts add keys; tighten them:

   ```sh
   ssh <user>@<nas> 'chmod 755 ~ && chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys'
   ```

5. **Docker without a password prompt (optional):** only root can use the Docker socket, and `sudo` asks for a password. To run the scripts unattended, allow exactly the three commands they need:

   ```sh
   echo '<user> ALL=(root) NOPASSWD: /usr/local/bin/docker, /var/packages/ContainerManager/target/usr/bin/docker-compose, /var/packages/Tailscale/target/bin/tailscale' \
     | sudo tee /etc/sudoers.d/flashcards >/dev/null && sudo chmod 440 /etc/sudoers.d/flashcards
   ```

   Docker access is equivalent to root on the NAS. A DSM update can remove the file; run the command again if `sudo` starts asking for a password.

6. **Tailscale:** Package Center → Tailscale → Install, open it and sign in. In the Tailscale admin console, turn on MagicDNS and HTTPS Certificates. Install Tailscale on each phone and computer that should reach the site, signed in to the same account.

## First install

Copy the source to the NAS. DSM has no `git`, so send a clean export from a checkout:

```sh
git archive --format=tar HEAD | ssh <user>@<nas> 'mkdir -p /volume1/docker/flash-cards/src && tar -xf - -C /volume1/docker/flash-cards/src'
```

On the NAS, create `.env` from the template and fill it in (see the comments in `.env.example`):

```sh
cd /volume1/docker/flash-cards/src/deploy/compose
cp .env.example .env && chmod 600 .env
```

The settings that matter on a Synology:

```sh
POSTGRES_PASSWORD=<openssl rand -hex 32>
FLASHCARDS_PGDATA=/volume1/docker/flash-cards/pgdata
FLASHCARDS_BACKUP_DIR=/volume1/docker/flash-cards/backups
FLASHCARDS_PUBLIC_BASE_URL=https://<nas-name>.<tailnet>.ts.net
```

Then build and start. The first build takes a while on a Celeron:

```sh
COMPOSE="sudo docker-compose" DOCKER="sudo docker" ./upgrade.sh
```

The site is now on `127.0.0.1:8000` on the NAS. Publish it on the tailnet with HTTPS:

```sh
sudo /var/packages/Tailscale/target/bin/tailscale serve --bg http://127.0.0.1:8000
```

`tailscale serve status` shows the address, `https://<nas-name>.<tailnet>.ts.net`. It is reachable only from devices on the tailnet, and the setting survives restarts. Mint the first admin invite as described in [The first admin account](self-hosting-compose.md#the-first-admin-account), with `sudo docker-compose` in place of `docker compose`.

## Backups

The `backup` service writes a verified backup to `/volume1/docker/flash-cards/backups` when it starts and every 24 hours, keeping the newest 14. That protects against mistakes and bad upgrades, but not against losing the volume. Copy the backups off the NAS too:

- **Hyper Backup:** back up `/volume1/docker/flash-cards/backups` and `/volume1/docker/flash-cards/src/deploy/compose/.env` to a USB disk or a cloud target, with encryption on. Do not back up `pgdata` itself: copying the files of a running database does not give a consistent copy. The dumps are the backup.
- Keep `.env` with the backups. It holds `POSTGRES_PASSWORD` and the two-factor encryption keys.

Test a restore after the first backup and from time to time, as described in the compose runbook's [Restore](self-hosting-compose.md#restore) section, using `COMPOSE="sudo docker-compose"`.

## Upgrades

From a checkout of the new release, send the source again and run the upgrade. It backs up first, then builds, migrates and waits for the API to be healthy:

```sh
git archive --format=tar HEAD | ssh <user>@<nas> 'tar -xf - -C /volume1/docker/flash-cards/src'
ssh <user>@<nas> 'cd /volume1/docker/flash-cards/src/deploy/compose && COMPOSE="sudo docker-compose" DOCKER="sudo docker" ./upgrade.sh'
```

`.env`, `pgdata` and `backups` are never part of the export, so an upgrade does not touch them. Files removed from the repository stay behind in `src`; they are harmless.

## Troubleshooting

- **`permission denied` on `/var/run/docker.sock`:** the command ran without `sudo`. Set `COMPOSE` and `DOCKER` as shown.
- **`sudo` asks for a password again after a DSM update:** the update removed `/etc/sudoers.d/flashcards`; recreate it.
- **The site is not reachable over Tailscale:** check `tailscale serve status` on the NAS, and that the device is signed in to the same tailnet. HTTPS needs MagicDNS and HTTPS Certificates turned on in the admin console.
