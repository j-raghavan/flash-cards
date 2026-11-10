FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY apps/api/pyproject.toml ./
COPY apps/api/src ./src
RUN pip install .

# Migration assets (not part of the installed package) and the entrypoint.
COPY apps/api/alembic.ini ./alembic.ini
COPY apps/api/migrations ./migrations
COPY apps/api/entrypoint.sh ./entrypoint.sh
# Readable by the unprivileged user whatever the build host's permissions were:
# a checkout on a Synology share, for example, can be owner-only (mode 700).
RUN chmod -R a+rX /app && chmod a+x ./entrypoint.sh \
    && useradd --system --no-create-home appuser
USER appuser

ENV FLASHCARDS_ENVIRONMENT=production
EXPOSE 8000

# Render and Fly set PORT; default to 8000 locally. The entrypoint runs
# `alembic upgrade head` first when FLASHCARDS_DATABASE_URL is set.
CMD ["./entrypoint.sh"]
