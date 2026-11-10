#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-only
#
# Restore the database from a backup made by backup.sh:
#
#   ./deploy/compose/restore.sh --yes backups/flashcards-YYYYMMDD-HHMMSS.dump
#
# This REPLACES everything in the database with the backup's contents. It stops
# the API, web and backup services, restores into the running database
# (objects are dropped and recreated), then starts the stack again, which
# migrates a backup taken on an older release up to the current schema.
# COMPOSE overrides the compose command, as in upgrade.sh.

set -eu

COMPOSE=${COMPOSE:-docker compose}

if [ "${1:-}" != "--yes" ] || [ -z "${2:-}" ]; then
	echo "usage: $0 --yes <backup.dump>"
	echo "Replaces the whole database with the backup. Take a fresh backup first if"
	echo "the current data might still be needed."
	exit 2
fi
dump=$(cd "$(dirname "$2")" && pwd)/$(basename "$2")
if [ ! -f "$dump" ]; then
	echo "restore: no such file: $2"
	exit 1
fi

cd "$(dirname "$0")"

echo "restore: stopping the API, web and backup services"
$COMPOSE stop api web backup
$COMPOSE up -d db
until $COMPOSE exec -T db pg_isready --quiet -U flashcards -d flashcards; do
	sleep 2
done

echo "restore: restoring $(basename "$dump")"
restore_args="-h db -U flashcards -d flashcards --clean --if-exists --no-owner --exit-on-error"
name=$(basename "$dump")
# A dump in the backup directory is read inside the backup container, where it
# is mounted at /backups: backups are owned by root with mode 600, so the
# invoking user may not be able to read them. Any other file is streamed in.
if $COMPOSE run --rm --no-deps -T --entrypoint test backup -f "/backups/$name" 2>/dev/null; then
	# shellcheck disable=SC2086
	$COMPOSE run --rm --no-deps -T --entrypoint pg_restore backup $restore_args "/backups/$name"
else
	# shellcheck disable=SC2086
	$COMPOSE run --rm --no-deps -T --entrypoint pg_restore backup $restore_args <"$dump"
fi

echo "restore: starting the stack"
$COMPOSE up -d
echo "restore: done. Sessions in the backup are valid again; see the runbook to revoke them."
