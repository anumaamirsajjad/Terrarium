"""FastAPI application factory.

This module is the composition root: it is where configuration, routers, and middleware
are wired together. Keep business logic out of it.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from terrarium import __version__
from terrarium.api.routes import health
from terrarium.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the app. Accepts injected settings so tests can override configuration."""
    injected = settings
    settings = settings or get_settings()

    app = FastAPI(
        title="Terrarium API",
        description="Neighbourhood-scale climate intervention digital twin.",
        version=__version__,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Routes resolve settings through `Depends(get_settings)`, which returns the cached
    # global. Without this override an injected Settings would reach the middleware above
    # and nothing else, so every endpoint would silently answer from the real config.
    if injected is not None:
        app.dependency_overrides[get_settings] = lambda: injected

    app.include_router(health.router)

    return app


app = create_app()


def run() -> None:
    """Console-script entrypoint: `uv run terrarium-api`."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "terrarium.api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.env == "dev",
    )
