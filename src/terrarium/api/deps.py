"""Shared FastAPI dependencies.

The runtime is built once in the app factory and stashed on `app.state`. Routes reach it
through this dependency rather than a module-level global, so a test can stand up an app
with a synthetic cube without any monkeypatching.
"""

from __future__ import annotations

from typing import cast

from fastapi import HTTPException, Request

from terrarium.api.runtime import Runtime

RUNTIME_ATTR = "terrarium_runtime"
STARTUP_ERROR_ATTR = "terrarium_startup_error"


def get_runtime(request: Request) -> Runtime:
    """The loaded cube and model, or 503 with the reason they are missing.

    503, not 404: the routes exist and the deployment is simply not ready. A 404 —
    which is what not mounting the routers produced — tells a client the endpoint does
    not exist, so a missing cube is indistinguishable from a typo'd URL or a version
    that never had `/simulate`. The distinction matters most to the frontend, which has
    to decide between "show a retry" and "this build cannot do that".
    """
    runtime = cast("Runtime | None", getattr(request.app.state, RUNTIME_ATTR, None))
    if runtime is None:
        reason = cast(
            "str | None", getattr(request.app.state, STARTUP_ERROR_ATTR, None)
        )
        raise HTTPException(
            status_code=503,
            detail=reason or "cube and model are not loaded; this deployment cannot serve data",
        )
    return runtime
