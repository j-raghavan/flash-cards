#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-only
#
# Database backups for the compose stack (runs in the "backup" service).
#
#   backup.sh          take a backup now, then one every BACKUP_INTERVAL_HOURS
#   backup.sh once     take one backup and exit (upgrade.sh uses this)
#
# Each backup is a pg_dump in the custom format, written to a temporary file,
# checked with pg_restore --list and only then renamed into place, so a failed
# or truncated dump never replaces a good one. The newest BACKUP_KEEP backups
# are kept; older ones are deleted. Files are readable by their owner only.

set -eu

BACKUP_DIR=${BACKUP_DIR:-/backups}
BACKUP_KEEP=${BACKUP_KEEP:-14}
BACKUP_INTERVAL_HOURS=${BACKUP_INTERVAL_HOURS:-24}
export PGHOST=${PGHOST:-db}
export PGUSER=${PGUSER:-flashcards}
export PGDATABASE=${PGDATABASE:-flashcards}

log() {
	echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) backup: $*"
}

case $BACKUP_KEEP in
'' | *[!0-9]* | 0)
	log "BACKUP_KEEP must be a whole number above 0"
	exit 2
	;;
esac
case $BACKUP_INTERVAL_HOURS in
'' | *[!0-9]* | 0)
	log "BACKUP_INTERVAL_HOURS must be a whole number above 0"
	exit 2
	;;
esac

umask 077
mkdir -p "$BACKUP_DIR"

backup_once() {
	stamp=$(date -u +%Y%m%d-%H%M%S)
	final="$BACKUP_DIR/flashcards-$stamp.dump"
	partial="$final.partial"
	if ! pg_dump --format=custom --file="$partial"; then
		rm -f "$partial"
		log "pg_dump failed"
		return 1
	fi
	if ! pg_restore --list "$partial" >/dev/null; then
		rm -f "$partial"
		log "the dump did not verify; kept the previous backups"
		return 1
	fi
	mv "$partial" "$final"
	log "wrote $(basename "$final") ($(du -h "$final" | cut -f1))"
	# Keep the newest BACKUP_KEEP dumps. Names sort by time, so a plain sort works.
	ls -1 "$BACKUP_DIR"/flashcards-*.dump 2>/dev/null | sort -r | tail -n +"$((BACKUP_KEEP + 1))" |
		while read -r old; do
			rm -f "$old"
			log "removed $(basename "$old")"
		done
	rm -f "$BACKUP_DIR"/*.partial
}

if [ "${1:-}" = "once" ]; then
	backup_once
	exit $?
fi

# Wait for the database before the first backup.
until pg_isready --quiet; do
	sleep 5
done
while :; do
	backup_once || log "will try again at the next interval"
	sleep "$((BACKUP_INTERVAL_HOURS * 3600))"
done
