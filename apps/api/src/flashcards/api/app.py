# SPDX-License-Identifier: AGPL-3.0-only
"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI

from flashcards import __version__
from flashcards.config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(
        title="Flash-Cards API",
        version=__version__,
        # No public docs in production; enable only in development.
        docs_url="/docs" if settings.environment == "development" else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.environment == "development" else None,
    )
    app.state.settings = settings

    @app.get("/healthz", tags=["ops"])
    def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return app


app = create_app()
