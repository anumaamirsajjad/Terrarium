"""FastAPI application factory.

This module is the composition root: it is where configuration, routers, and middleware
are wired together. Keep business logic out of it.

It is also the only place the cube and the trained model are opened. `cores/` takes both
as arguments so that loading them can happen exactly once, here, rather than per request.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from terrarium import __version__
from terrarium.api.deps import RUNTIME_ATTR, STARTUP_ERROR_ATTR
from terrarium.api.routes import agent, cube, evidence, explain, health, plan, simulate
from terrarium.api.runtime import Runtime, StartupError, load_runtime
from terrarium.config import Settings, get_settings

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    *,
    runtime: Runtime | None = None,
) -> FastAPI:
    """Build the app.

    Accepts injected settings so tests can override configuration, and an injected
    `runtime` so they can serve a synthetic in-memory cube without a Zarr store or a
    trained booster on disk.
    """
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

    loaded = runtime
    if loaded is None:
        try:
            loaded = load_runtime(settings)
        except StartupError as exc:
            # Deliberately not fatal. /health is what the frontend boots against and what
            # a container's readiness probe hits, so an app that cannot find its cube must
            # still start and say so - dying here turns a missing artefact into an opaque
            # crash loop.
            logger.error("cube/model unavailable; /health is up, data routes 503: %s", exc)
            setattr(app.state, STARTUP_ERROR_ATTR, str(exc))

    # Mounted either way. The routes exist in this build whether or not the artefacts
    # loaded, so `get_runtime` answers 503 with the reason rather than the router's
    # absence answering 404 - which would claim the endpoint itself does not exist.
    app.include_router(cube.router)
    app.include_router(simulate.router)
    # /plan/presets needs no cube, so the router is mounted regardless: a deployment
    # without artefacts can still show the costed intervention library.
    app.include_router(plan.router)
    # The search agent (Phase A). Needs the cube, so it gets the same 503-with-a-reason
    # treatment through `get_runtime` that /simulate does.
    app.include_router(agent.router)
    # The spatial explanation (Phase E). Needs the cube for the same reason /simulate does.
    app.include_router(explain.router)
    # `/evidence/ask` reads this repository's own markdown and needs no cube at all, so it
    # answers on a deployment whose Zarr store failed to load — which is precisely the
    # deployment where somebody most wants to ask the docs what went wrong.
    app.include_router(evidence.router)

    if loaded is not None:
        setattr(app.state, RUNTIME_ATTR, loaded)
        logger.info(
            "loaded cube %s (%d windows) and model %s",
            loaded.cube_path,
            len(loaded.windows),
            loaded.model_path,
        )

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
