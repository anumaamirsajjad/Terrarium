"""Reuse the API's synthetic runtime rather than building a second one.

`api/conftest.py` already stands up a structurally honest cube on the real canonical grid
and trains a genuine booster on it in about two seconds, session-scoped. The agent tests
need exactly that and nothing more, so this re-exports it. A second fixture would be a
second definition of "a plausible Lahore" for the same assertions to drift against.

`with_model` comes with it: the agent requires a key now, so every test here has to
configure a scripted provider, and it must be the *same* seam the route uses.
"""

from __future__ import annotations

from terrarium.api.conftest import (  # noqa: F401  (re-exported as pytest fixtures)
    ScriptedAdapter,
    client,
    no_ambient_keys,
    synthetic_runtime,
    with_model,
)
