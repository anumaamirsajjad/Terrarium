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
from collections import deque
from dataclasses import dataclass

import numpy as np

from terrarium.dsl.observe import Observation
from terrarium.state.grid import Grid

# Beyond this the oldest reports fall off the end. A bound rather than unbounded growth:
# this store is reachable from an HTTP endpoint, and an unbounded one is a memory leak with
# a public door on it.
DEFAULT_CAPACITY = 500


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


class ObservationStore:
    """A bounded, thread-safe list of observations that can render itself onto the grid.

    Thread-safe because uvicorn runs the app across threads for sync work, and a `deque`
    plus a counter is exactly the kind of state that looks atomic and is not.
    """

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        self._items: deque[StoredObservation] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._next_id = 1

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
