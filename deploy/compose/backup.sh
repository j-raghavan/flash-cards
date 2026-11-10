#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-only
#
# Database backups for the compose stack (runs in the "backup" service).
#
#   backup.sh              back up now unless a backup newer than the interval
#                          exists, then every BACKUP_INTERVAL_HOURS
#   backup.sh once         take one backup now and exit (upgrade.sh)
#   backup.sh pre-restore  take one safety backup named pre-restore-*.dump, which
#                          retention never deletes, and exit (restore.sh)
#
# Each backup is a pg_dump in the custom format, written under a name of its
# own, checked by reading the whole archive back (pg_restore to /dev/null), and
# only then renamed into place, so a failed or truncated dump never replaces a
# good one. The newest BACKUP_KEEP flashcards-*.dump files are kept; older ones
# are deleted. Files are readable by their owner only.

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

# A whole number from 1 to 9999, without leading zeros (which shell arithmetic
# would read as octal).
positive() {
	case $1 in
	'' | 0* | *[!0-9]*) return 1 ;;
	esac
	[ "${#1}" -le 4 ]
}

if ! positive "$BACKUP_KEEP"; then
	log "BACKUP_KEEP must be a whole number from 1 to 9999"
	exit 2
fi
if ! positive "$BACKUP_INTERVAL_HOURS"; then
	log "BACKUP_INTERVAL_HOURS must be a whole number from 1 to 9999"
	exit 2
fi

umask 077
mkdir -p "$BACKUP_DIR"

# backup_to PREFIX: write and verify PREFIX-<time>.dump. Returns non-zero on any
# failure, leaving earlier backups untouched.
backup_to() {
	prefix=$1
	stamp=$(date -u +%Y%m%d-%H%M%S)
	final="$BACKUP_DIR/$prefix-$stamp.dump"
	if [ -e "$final" ]; then
		final="$BACKUP_DIR/$prefix-$stamp-$$.dump"
	fi
	# A name of its own, so concurrent runs never touch each other's file.
	partial="$BACKUP_DIR/.partial-$stamp-$$"
	if ! pg_dump --format=custom --file="$partial"; then
		rm -f "$partial"
		log "pg_dump failed"
		return 1
	fi
	# Reads and decompresses every block, not just the table of contents.
	if ! pg_restore --file=/dev/null "$partial"; then
		rm -f "$partial"
		log "the dump did not verify; kept the previous backups"
		return 1
	fi
	if ! mv "$partial" "$final"; then
		rm -f "$partial"
		log "could not move the dump into place"
		return 1
	fi
	log "wrote $(basename "$final") ($(du -h "$final" | cut -f1))"
}

prune() {
	# Names sort by time, so a reverse sort lists the newest first.
	ls -1 "$BACKUP_DIR"/flashcards-*.dump 2>/dev/null | sort -r | tail -n +"$((BACKUP_KEEP + 1))" |
		while read -r old; do
			rm -f "$old"
			log "removed $(basename "$old")"
		done
	# Leftovers of runs that were killed; never another run's live file.
	find "$BACKUP_DIR" -maxdepth 1 -name '.partial-*' -mmin +1440 -exec rm -f {} + 2>/dev/null || true
}

backup_once() {
	backup_to flashcards && prune
}

recent_backup() {
	[ -n "$(find "$BACKUP_DIR" -maxdepth 1 -name 'flashcards-*.dump' -mmin -"$((BACKUP_INTERVAL_HOURS * 60))" 2>/dev/null)" ]
}

case ${1:-} in
once)
	backup_once
	exit $?
	;;
pre-restore)
	backup_to pre-restore
	exit $?
	;;
'') ;;
*)
	log "unknown command: $1"
	exit 2
	;;
esac

# Stop promptly on docker stop (the shell is PID 1 and would ignore SIGTERM).
trap 'exit 0' TERM INT

until pg_isready --quiet; do
	sleep 5
done
# A restart (reboot, upgrade, restore) does not add a backup when a recent one
# exists, so restarts cannot rotate the good backups out.
if recent_backup; then
	log "a backup from the last ${BACKUP_INTERVAL_HOURS}h exists; next one in ${BACKUP_INTERVAL_HOURS}h"
else
	backup_once || log "will try again at the next interval"
fi
while :; do
	sleep "$((BACKUP_INTERVAL_HOURS * 3600))" &
	wait $!
	backup_once || log "will try again at the next interval"
done
