"""FastAPI application factory.

This module is the composition root: it is where configuration, routers, and middleware
are wired together. Keep business logic out of it.

It is also the only place the cube and the trained model are opened. `cores/` takes both
as arguments so that loading them can happen exactly once, here, rather than per request.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from terrarium import __version__
from terrarium.api.deps import RUNTIME_ATTR, STARTUP_ERROR_ATTR
from terrarium.api.routes import agent, chat, cube, explain, health, plan, simulate
from terrarium.api.runtime import Runtime, StartupError, load_runtime
from terrarium.config import Settings, get_settings

logger = logging.getLogger(__name__)


def _scrub_non_finite(value: Any) -> Any:
    """Replace a `NaN`/`Infinity` float with its `repr` so a structure containing one can
    still be JSON-encoded (F22).

    `allow_inf_nan=False` on a schema's float fields rejects the value, but the resulting
    `RequestValidationError` still echoes the raw rejected input in its `"input"` field -
    that is the whole point of a validation error - and `NaN` has no JSON spelling.
    Starlette's `JSONResponse` encodes strictly, so that echo used to fail to serialise
    and turn a would-be 422 into an unrelated 500. Scrubbing here, once, for every route,
    is the belt to the field setting's braces: it also covers a non-finite float on a
    field with no `allow_inf_nan` constraint at all.
    """
    if isinstance(value, float) and not math.isfinite(value):
        return repr(value)
    if isinstance(value, dict):
        return {k: _scrub_non_finite(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub_non_finite(v) for v in value]
    return value


def _register_validation_error_handler(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def _validation_error_response(_: Request, exc: RequestValidationError) -> JSONResponse:
        # `jsonable_encoder` first, same as FastAPI's own default handler: an error's `ctx`
        # can carry the raised exception object itself (e.g. from a `model_validator`),
        # which needs converting before it is JSON-safe at all - scrubbing alone is not
        # that step.
        errors = jsonable_encoder(exc.errors())
        return JSONResponse(status_code=422, content={"detail": _scrub_non_finite(errors)})


# Anything above ~1 MB is not a polygon a person drew by hand (F21): the mask this
# produces is a fixed cell count for the tile whatever the request's own resolution, so a
# 100 MB body buys a client nothing but CPU spent parsing it.
MAX_BODY_BYTES = 1_000_000


class BodySizeLimitMiddleware:
    """Reject a request whose declared body size exceeds `MAX_BODY_BYTES`, before FastAPI
    reads or parses it.

    Pure ASGI rather than Starlette's `BaseHTTPMiddleware`, which buffers the whole body
    into memory before a handler ever sees it — exactly the cost this guard exists to
    avoid paying on a 100 MB request.

    # ponytail: trusts the `Content-Length` header rather than also metering a chunked
    # body with none, since every real client here (the frontend, curl, requests) sends
    # one. Add streaming enforcement if an adversarial chunked upload becomes a real path.
    """

    def __init__(self, app: Any, *, max_bytes: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None and int(declared) > self.max_bytes:
            await send(
                {
                    "type": "http.response.start",
                    "status": 413,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": b'{"detail":"request body too large"}',
                }
            )
            return

        await self.app(scope, receive, send)


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

    _register_validation_error_handler(app)
    app.add_middleware(BodySizeLimitMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        # F23: nothing in this project uses cookies or HTTP auth, and that is what makes
        # a wildcard origin dangerous with credentials on - the two together are a CSRF
        # hole for the same routes, from any origin, on someone else's browser tab.
        allow_credentials=False,
        # Narrowed from "*": this API implements GET and POST (and OPTIONS for the
        # preflight); advertising DELETE, PUT and PATCH invites a client to expect a
        # method that 404s.
        allow_methods=["GET", "POST", "OPTIONS"],
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
    # without artefacts can still show the intervention library.
    app.include_router(plan.router)
    # The search agent (Phase A). Needs the cube, so it gets the same 503-with-a-reason
    # treatment through `get_runtime` that /simulate does.
    app.include_router(agent.router)
    # The spatial explanation (Phase E). Needs the cube for the same reason /simulate does.
    app.include_router(explain.router)
    # `/simulate/chat` needs no cube at all — it only ever reasons over a brief the client
    # already has — but it does need a model, same as /agent/search.
    app.include_router(chat.router)

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
