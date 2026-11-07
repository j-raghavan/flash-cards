FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY apps/api/pyproject.toml ./
COPY apps/api/src ./src
RUN pip install .

RUN useradd --system --no-create-home appuser
USER appuser

ENV FLASHCARDS_ENVIRONMENT=production
EXPOSE 8000

# Render and Fly set PORT; default to 8000 locally.
CMD ["sh", "-c", "uvicorn flashcards.api.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
