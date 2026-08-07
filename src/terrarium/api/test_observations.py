"""The observation store, and how it renders onto the grid."""

from __future__ import annotations

import numpy as np

from terrarium.api.observations import ObservationStore, RateLimiter
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


# ------------------------------------------------------------------------------------
# RateLimiter — the ceiling on free-tier spend (A21)
# ------------------------------------------------------------------------------------


def test_calls_are_allowed_up_to_the_limit_then_refused() -> None:
    limiter = RateLimiter(limit=3, window_s=60.0)
    assert [limiter.allow("1.2.3.4", now=0.0) for _ in range(3)] == [True, True, True]
    assert limiter.allow("1.2.3.4", now=0.0) is False


def test_callers_are_counted_separately() -> None:
    """One caller exhausting the tier must not lock out everybody else."""
    limiter = RateLimiter(limit=2, window_s=60.0)
    assert limiter.allow("1.1.1.1", now=0.0) and limiter.allow("1.1.1.1", now=0.0)
    assert limiter.allow("1.1.1.1", now=0.0) is False
    assert limiter.allow("2.2.2.2", now=0.0) is True


def test_the_window_rolls_rather_than_resetting_on_a_boundary() -> None:
    limiter = RateLimiter(limit=2, window_s=60.0)
    assert limiter.allow("a", now=0.0) and limiter.allow("a", now=30.0)
    assert limiter.allow("a", now=59.0) is False
    # The 0.0 hit has aged out by t=61; the 30.0 one has not, so exactly one slot frees up.
    assert limiter.allow("a", now=61.0) is True
    assert limiter.allow("a", now=61.0) is False


def test_refused_calls_do_not_extend_the_window() -> None:
    """A refusal must not count as a hit, or hammering the endpoint would never recover."""
    limiter = RateLimiter(limit=1, window_s=60.0)
    assert limiter.allow("a", now=0.0) is True
    for t in (10.0, 20.0, 30.0, 50.0):
        assert limiter.allow("a", now=t) is False
    assert limiter.allow("a", now=61.0) is True


def test_the_caller_table_stays_bounded() -> None:
    """A dict keyed on a stranger-supplied value is a memory leak with a public door."""
    limiter = RateLimiter(limit=5, window_s=60.0, max_keys=50)
    for i in range(500):
        # Each caller is one-shot and its window has long expired by the time the next
        # batch arrives, so nothing here should be retained.
        limiter.allow(f"caller-{i}", now=float(i) * 100.0)
    assert len(limiter._hits) <= 51
