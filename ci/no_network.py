"""A pytest plugin that fails any test which opens a network socket.

`CLAUDE.md` says **no test may touch the network**, and that rule is what makes the suite
CI-safe. It was broken exactly once, and the interesting part is how it presented:
`test_a_lazy_read_failure_is_caught_and_isolated` *passed* — for the wrong reason. A green
suite cannot report that class of failure, which is why the rule needs an enforcer rather
than a reviewer.

Loaded with `pytest -p no_network` (see `.github/workflows/ci.yml`) so the patch lands
before any test module is imported. Not in a `conftest.py` on purpose: this is a CI gate,
not a fixture, and a developer running `uv run pytest` locally should not have their
sockets monkeypatched as a side effect of collecting tests.

Loopback stays allowed — FastAPI's `TestClient` speaks to the app in-process over it, so
blocking it would fail every route test for a reason that has nothing to do with the rule.
"""

from __future__ import annotations

import socket
from typing import Any

_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex

# Loopback only. Anything that resolves elsewhere is a genuine outbound call.
_ALLOWED_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "0.0.0.0"})


def _host_of(address: Any) -> str | None:
    """The hostname from any of the address shapes `connect` accepts.

    AF_UNIX passes a plain string path and AF_INET/AF_INET6 pass a tuple. A path is not a
    network destination, so it returns None and is allowed through.
    """
    if isinstance(address, tuple) and address:
        host = address[0]
        return host if isinstance(host, str) else None
    return None


def _refuse(host: str) -> RuntimeError:
    return RuntimeError(
        f"a test tried to reach {host!r} over the network. No test may touch the "
        "network (CLAUDE.md) — use a fixture or a recorded payload. Only `ingest/` is "
        "allowed to do network I/O at all, and its tests stub the transport."
    )


def _guarded_connect(self: socket.socket, address: Any, *args: Any, **kwargs: Any) -> Any:
    host = _host_of(address)
    if host is not None and host not in _ALLOWED_HOSTS:
        raise _refuse(host)
    return _real_connect(self, address, *args, **kwargs)


def _guarded_connect_ex(self: socket.socket, address: Any, *args: Any, **kwargs: Any) -> Any:
    host = _host_of(address)
    if host is not None and host not in _ALLOWED_HOSTS:
        raise _refuse(host)
    return _real_connect_ex(self, address, *args, **kwargs)


socket.socket.connect = _guarded_connect  # type: ignore[method-assign]
socket.socket.connect_ex = _guarded_connect_ex  # type: ignore[method-assign]
