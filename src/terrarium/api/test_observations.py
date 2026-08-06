"""The observation store, and how it renders onto the grid."""

from __future__ import annotations

import numpy as np

from terrarium.api.observations import ObservationStore
from terrarium.config import ACTIVE_TILE
from terrarium.dsl.observe import Observation, ObservationCategory
from terrarium.state.grid import grid_for_tile

GRID = grid_for_tile(ACTIVE_TILE)


def _observation(severity: int) -> Observation:
    return Observation(
        category=ObservationCategory.SHADE_DEFICIT,
        description="bare concrete, no canopy",
        severity=severity,
        confidence=0.7,
    )


def test_ids_are_assigned_in_order() -> None:
    store = ObservationStore()
    first = store.add(_observation(3), lon=74.3, lat=31.5, row=10, col=10)
    second = store.add(_observation(3), lon=74.3, lat=31.5, row=10, col=11)
    assert (first.id, second.id) == (1, 2)


def test_unreported_cells_are_nan_not_zero() -> None:
    # 0 would draw the whole tile as "reported, nothing wrong", when 40,000 of its cells
    # have simply never been photographed. Every other layer this API serves uses NaN for
    # "no data" and this one must not be the exception.
    store = ObservationStore()
    store.add(_observation(3), lon=74.3, lat=31.5, row=5, col=5)
    raster = store.severity_raster(GRID)

    assert raster.shape == GRID.shape
    assert raster[5, 5] == 3
    assert np.isnan(raster[0, 0])
    assert int(np.isfinite(raster).sum()) == 1


def test_a_cell_reports_its_worst_severity_not_its_average() -> None:
    # Two shaded streets and one burning waste pile average to something meaningless.
    store = ObservationStore()
    store.add(_observation(1), lon=74.3, lat=31.5, row=7, col=7)
    store.add(_observation(5), lon=74.3, lat=31.5, row=7, col=7)
    store.add(_observation(2), lon=74.3, lat=31.5, row=7, col=7)

    assert store.severity_raster(GRID)[7, 7] == 5


def test_the_store_is_bounded() -> None:
    # It is reachable from an HTTP endpoint, so unbounded growth is a memory leak with a
    # public door on it.
    store = ObservationStore(capacity=3)
    for index in range(5):
        store.add(_observation(2), lon=74.3, lat=31.5, row=index, col=0)

    assert len(store.all()) == 3
    assert [item.id for item in store.all()] == [3, 4, 5]


def test_an_empty_store_renders_an_all_nan_raster() -> None:
    raster = ObservationStore().severity_raster(GRID)
    assert raster.shape == GRID.shape
    assert not np.isfinite(raster).any()
