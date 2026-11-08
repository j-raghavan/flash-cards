#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-only
# Container entrypoint: apply migrations (when a database is configured), then serve.
set -e

if [ -n "${FLASHCARDS_DATABASE_URL}" ]; then
  echo "Applying database migrations..."
  alembic upgrade head
else
  echo "FLASHCARDS_DATABASE_URL not set; starting in in-memory mode (no migrations)."
fi

exec uvicorn flashcards.api.app:app --host 0.0.0.0 --port "${PORT:-8000}"
