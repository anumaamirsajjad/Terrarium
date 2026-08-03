"""Shared FastAPI dependencies.

The runtime is built once in the app factory and stashed on `app.state`. Routes reach it
through this dependency rather than a module-level global, so a test can stand up an app
with a synthetic cube without any monkeypatching.
"""

from __future__ import annotations

from typing import cast

from fastapi import Request

from terrarium.api.runtime import Runtime

RUNTIME_ATTR = "terrarium_runtime"


def get_runtime(request: Request) -> Runtime:
    """The loaded cube and model. Present for the app's whole lifetime."""
    runtime = cast("Runtime | None", getattr(request.app.state, RUNTIME_ATTR, None))
    if runtime is None:
        # Only reachable if the app was built without a runtime and a route was called
        # anyway - a wiring bug, not a user error.
        raise RuntimeError("no runtime on app.state; create_app did not load one")
    return runtime
