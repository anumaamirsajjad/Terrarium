"""The evidence route requires a key, so its tests need the scripted seam.

Re-exported from `api/conftest.py` so there is one definition of "a configured provider"
and one synthetic cube, rather than a copy per package.
"""

from __future__ import annotations

from terrarium.api.conftest import (  # noqa: F401  (re-exported as pytest fixtures)
    ScriptedAdapter,
    client,
    no_ambient_keys,
    with_model,
)
