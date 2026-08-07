"""Where citizen observations live: in memory, on the grid, beside the cube — not in it.

The cube is measurements. Every variable in it comes from a satellite, a reanalysis or an
open dataset with a known instrument and a known error, and `state/` guarantees they share
one grid. A language model's reading of a phone photo has none of those properties, so it
gets the grid and nothing else: the same 201x202 cells, its own layer, its own endpoint,
and no route into `cube.zarr`. That separation is the reason this can be shown on the same
map without making "what the cube says" an unanswerable question.

**In-process and unpersisted, deliberately.** Observations vanish on restart. Persisting
them would mean owning user-submitted content — moderation, deletion, retention — which is
outside this project's scope in the same way user accounts are, and a demo needs the
mechanism, not the database.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass

import numpy as np

from terrarium.dsl.observe import Observation
from terrarium.state.grid import Grid

# Beyond this the oldest reports fall off the end. A bound rather than unbounded growth:
# this store is reachable from an HTTP endpoint, and an unbounded one is a memory leak with
# a public door on it.
DEFAULT_CAPACITY = 500

# What one caller may spend, per hour. The store's capacity bounds memory and the base64
# field bounds one request; neither bounds **spend against a rate-limited free tier**, and
# `POST /observations` is the one route that calls a paid-shaped API without auth in front
# of it. A ceiling, not authentication - that is scope (CLAUDE.md).
DEFAULT_RATE_LIMIT = 20
RATE_LIMIT_WINDOW_S = 3600.0
# Distinct callers tracked at once. Same argument as the store's own bound: a dict keyed by
# something a stranger supplies is a memory leak with a public door on it.
MAX_TRACKED_CALLERS = 10_000


@dataclass(frozen=True)
class StoredObservation:
    """One observation, placed on the grid.

    `row`/`col` are assigned by the API from the submitted coordinates — never by the
    model, which is shown the pixels and not the location and has no business inventing
    one.
    """

    id: int
    observation: Observation
    lon: float
    lat: float
    row: int
    col: int


class RateLimiter:
    """A per-caller ceiling on calls per rolling window. In-process, like the store.

    Guards **spend**, not access: `POST /observations` is the only route that reaches a
    rate-limited free tier, and it takes no auth (that is a permanent scope boundary). A
    caller who exhausts the free tier takes the feature down for everybody, and the failure
    arrives as somebody else's quota error rather than as anything this project logged.

    Keyed on whatever the caller is identified by, which behind a proxy is the proxy — so
    this is a ceiling on obvious abuse, not a defence against a distributed one. That is the
    right size for it: the thing being protected is a free API key, not a database.
    """

    def __init__(
        self,
        limit: int = DEFAULT_RATE_LIMIT,
        window_s: float = RATE_LIMIT_WINDOW_S,
        max_keys: int = MAX_TRACKED_CALLERS,
    ) -> None:
        self._limit = limit
        self._window_s = window_s
        self._max_keys = max_keys
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    @property
    def limit(self) -> int:
        return self._limit

    def allow(self, key: str, *, now: float | None = None) -> bool:
        """Record a call and say whether it was within the ceiling.

        `now` is injectable so the window can be tested without sleeping through an hour.
        """
        moment = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            cutoff = moment - self._window_s
            while hits and hits[0] <= cutoff:
                hits.popleft()

            if len(hits) >= self._limit:
                return False

            hits.append(moment)
            # Drop callers whose window has fully expired before admitting a new key, so a
            # stream of one-shot callers cannot grow this dict without bound.
            if len(self._hits) > self._max_keys:
                for stale in [k for k, v in self._hits.items() if not v or v[-1] <= cutoff]:
                    del self._hits[stale]
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


class ObservationStore:
    """A bounded, thread-safe list of observations that can render itself onto the grid.

    Thread-safe because uvicorn runs the app across threads for sync work, and a `deque`
    plus a counter is exactly the kind of state that looks atomic and is not.
    """

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        self._items: deque[StoredObservation] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._next_id = 1
        # Created with the store so a route cannot wire one and forget the other.
        self.rate_limiter = RateLimiter()

    def add(
        self, observation: Observation, *, lon: float, lat: float, row: int, col: int
    ) -> StoredObservation:
        with self._lock:
            stored = StoredObservation(
                id=self._next_id,
                observation=observation,
                lon=lon,
                lat=lat,
                row=row,
                col=col,
            )
            self._next_id += 1
            self._items.append(stored)
            return stored

    def all(self) -> tuple[StoredObservation, ...]:
        with self._lock:
            return tuple(self._items)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def severity_raster(self, grid: Grid) -> np.ndarray:
        """Worst reported severity per cell, NaN where nobody reported anything.

        The **maximum**, not the mean or the count. Two reports of a shaded street and one
        of a burning waste pile average to something meaningless, and a count answers "who
        is reporting" rather than "what is wrong" — which is a question about the
        photographers, not the street.

        NaN rather than 0 for unreported cells, matching every other layer this API serves:
        0 would draw the whole tile as "reported, nothing wrong", when 40,000 of its cells
        have simply never been photographed.
        """
        raster = np.full(grid.shape, np.nan, dtype="float32")
        for stored in self.all():
            severity = float(stored.observation.severity)
            current = raster[stored.row, stored.col]
            if not np.isfinite(current) or severity > current:
                raster[stored.row, stored.col] = severity
        return raster
