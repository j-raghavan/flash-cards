#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-only
#
# Upgrade (or first start) the compose stack without losing data:
#
#   1. if the database is running, take a verified backup, and stop if it fails;
#   2. build the images from the current source;
#   3. start the stack: the migrate service applies new migrations before the
#      API starts;
#   4. wait until the API reports healthy.
#
# If a migration fails, the API stays down and the logs are printed; the backup
# from step 1 is the way back (restore.sh, with the previous release).
#
# Run it from anywhere: ./deploy/compose/upgrade.sh
# COMPOSE and DOCKER override the commands, e.g. on a Synology:
#   COMPOSE="sudo docker-compose" DOCKER="sudo docker" ./upgrade.sh

set -eu

cd "$(dirname "$0")"
COMPOSE=${COMPOSE:-docker compose}
DOCKER=${DOCKER:-docker}
WAIT_SECONDS=${WAIT_SECONDS:-300}

say() {
	echo "upgrade: $*"
}

if [ ! -f .env ]; then
	say "no .env here; copy .env.example to .env and fill it in first"
	exit 1
fi

# Fails loudly (e.g. sudo needing a password) instead of reading as "not running".
db=$($COMPOSE ps --status running --quiet db)
if [ -n "$db" ]; then
	say "taking a backup before upgrading"
	# A one-off container: works whatever state the backup service is in, and
	# before the first upgrade that adds it.
	$COMPOSE run --rm --no-deps -T backup once
else
	say "the database is not running, so there is nothing to back up yet"
fi

say "building images"
$COMPOSE build

say "starting the stack (migrations run first)"
if ! $COMPOSE up -d --remove-orphans; then
	say "the stack did not start; recent logs follow"
	$COMPOSE logs --tail 40 migrate api
	say "to roll back: check out the previous release and run restore.sh with the backup taken above"
	exit 1
fi

waited=0
while :; do
	api=$($COMPOSE ps --quiet api)
	health=""
	if [ -n "$api" ]; then
		health=$($DOCKER inspect --format '{{.State.Health.Status}}' "$api" 2>/dev/null || true)
	fi
	if [ "$health" = "healthy" ]; then
		break
	fi
	if [ "$waited" -ge "$WAIT_SECONDS" ]; then
		say "the API is not healthy after ${WAIT_SECONDS}s; recent logs follow"
		$COMPOSE logs --tail 40 migrate api
		exit 1
	fi
	sleep 5
	waited=$((waited + 5))
done

say "done: the API is healthy"
$COMPOSE ps
