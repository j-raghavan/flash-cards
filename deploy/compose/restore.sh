#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-only
#
# Restore the database from a backup made by backup.sh:
#
#   ./deploy/compose/restore.sh --yes backups/flashcards-YYYYMMDD-HHMMSS.dump
#
# This REPLACES everything in the database with the backup's contents:
#
#   1. stops the API, web and backup services, so nothing writes meanwhile;
#   2. saves the current database as backups/pre-restore-*.dump (never deleted
#      by retention), and stops if that fails, unless --no-safety-backup;
#   3. drops and recreates the database, so nothing from a newer schema is left
#      behind, then restores the backup in one transaction: a failure leaves an
#      empty database, never a half-restored one;
#   4. starts the stack, which migrates a backup from an older release up to the
#      current schema.
#
# Run it with the release that made the backup (or a newer one). COMPOSE
# overrides the compose command, as in upgrade.sh.

set -eu

COMPOSE=${COMPOSE:-docker compose}

usage() {
	echo "usage: $0 --yes [--no-safety-backup] <backup.dump>"
	echo "Replaces the whole database with the backup. The current data is saved"
	echo "first as backups/pre-restore-*.dump."
	exit 2
}

[ "${1:-}" = "--yes" ] || usage
shift
safety=yes
if [ "${1:-}" = "--no-safety-backup" ]; then
	safety=no
	shift
fi
[ -n "${1:-}" ] || usage
dump_dir=$(cd "$(dirname "$1")" && pwd -P)
name=$(basename "$1")
dump="$dump_dir/$name"
if [ ! -f "$dump" ]; then
	echo "restore: no such file: $1"
	exit 1
fi

cd "$(dirname "$0")"

# The backup directory as the backup service mounts it, resolved like the dump.
backup_dir=$(grep -E '^FLASHCARDS_BACKUP_DIR=' .env 2>/dev/null | tail -n 1 | cut -d= -f2- || true)
backup_dir=${backup_dir:-./backups}
backup_dir=$(cd "$backup_dir" 2>/dev/null && pwd -P || echo "")

on_failure() {
	echo "restore: FAILED. The API, web and backup services are stopped."
	echo "restore: fix the cause and run this again, or start the stack as it is with:"
	echo "restore:   $COMPOSE up -d"
}
trap 'status=$?; [ "$status" -eq 0 ] || on_failure' EXIT

echo "restore: stopping the API, web and backup services"
$COMPOSE stop api web backup
$COMPOSE up -d db
waited=0
until $COMPOSE exec -T db pg_isready --quiet -U flashcards -d postgres; do
	if [ "$waited" -ge 120 ]; then
		echo "restore: the database did not start within 120s"
		exit 1
	fi
	sleep 2
	waited=$((waited + 2))
done

if [ "$safety" = yes ]; then
	echo "restore: saving the current database first"
	$COMPOSE run --rm --no-deps -T backup pre-restore
fi

echo "restore: recreating the database"
$COMPOSE exec -T db dropdb -U flashcards --if-exists --force flashcards
$COMPOSE exec -T db createdb -U flashcards flashcards

echo "restore: restoring $name"
restore_args="-h db -U flashcards -d flashcards --no-owner --single-transaction --exit-on-error"
if [ -n "$backup_dir" ] && [ "$dump_dir" = "$backup_dir" ]; then
	# Read inside the backup container, where the directory is mounted at
	# /backups: backups are owned by root with mode 600, which the invoking user
	# may not be able to read.
	# shellcheck disable=SC2086
	$COMPOSE run --rm --no-deps -T --entrypoint pg_restore backup $restore_args "/backups/$name"
else
	# shellcheck disable=SC2086
	$COMPOSE run --rm --no-deps -T --entrypoint pg_restore backup $restore_args <"$dump"
fi

echo "restore: starting the stack"
$COMPOSE up -d
echo "restore: done. Sessions in the backup are valid again; see the runbook to revoke them."
