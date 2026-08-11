"""Deterministic segmentation on a synthetic delta, plus the route over the real cores.

The segmentation is the half a model must not touch. Everything the description is allowed
to say comes out of this table, so the table has to be right on its own — which is exactly
what a test with no model in it can establish.
"""

from __future__ import annotations

import numpy as np
import xarray as xr

from terrarium.api.explain_spatial import NEGLIGIBLE_C, as_table, explain_pattern
from terrarium.api.runtime import Runtime
from terrarium.state.cube import select_window
from terrarium.state.grid import Grid


def _setup(synthetic_runtime: Runtime) -> tuple[xr.Dataset, str, Grid]:
    label = synthetic_runtime.default_window()
    return select_window(synthetic_runtime.cube, label), label, synthetic_runtime.grid


def _mask(grid: Grid, rows: slice, cols: slice) -> np.ndarray:
    mask = np.zeros(grid.shape, dtype=bool)
    mask[rows, cols] = True
    return mask


def test_only_regions_that_changed_are_reported(synthetic_runtime: Runtime) -> None:
    """An intervention touches a handful of blocks. Reporting the other hundred as
    'no change' buries the answer in its own negative space."""
    window, label, grid = _setup(synthetic_runtime)

    mask = _mask(grid, slice(20, 40), slice(40, 60))
    delta = np.zeros(grid.shape, dtype="float32")
    delta[mask] = -1.0

    regions = explain_pattern(
        window=window,
        label=label,
        grid=grid,
        delta=delta,
        canopy_added=np.where(mask, 0.3, 0.0),
        mask=mask,
    )

    assert len(regions) == 1
    assert regions[0].region_id == "r01c02"
    assert regions[0].inside_polygon
    assert not regions[0].spillover


def test_cooling_is_reported_corrected_not_raw(synthetic_runtime: Runtime) -> None:
    """-1.0 degC of model output is 0.4 degC of expectation. A description quoting the
    raw delta would be the one place in the product the hindcast correction is skipped."""
    window, label, grid = _setup(synthetic_runtime)

    mask = _mask(grid, slice(20, 40), slice(40, 60))
    delta = np.zeros(grid.shape, dtype="float32")
    delta[mask] = -1.0

    regions = explain_pattern(
        window=window, label=label, grid=grid, delta=delta,
        canopy_added=np.where(mask, 0.3, 0.0), mask=mask,
    )

    assert regions[0].expected_cooling_c == 0.4


def test_spillover_is_distinguished_from_the_drawn_area(synthetic_runtime: Runtime) -> None:
    """The part of the pattern a reader is most likely to think is a bug. It is real
    physics — the 500 m neighbourhood terms — and the table has to name it as such."""
    window, label, grid = _setup(synthetic_runtime)

    mask = _mask(grid, slice(20, 40), slice(40, 60))
    delta = np.zeros(grid.shape, dtype="float32")
    delta[mask] = -1.0
    # A neighbouring block, changed without being drawn on.
    delta[20:40, 60:80] = -0.5

    regions = explain_pattern(
        window=window, label=label, grid=grid, delta=delta,
        canopy_added=np.where(mask, 0.3, 0.0), mask=mask,
    )
    by_id = {region.region_id: region for region in regions}

    assert by_id["r01c02"].inside_polygon and not by_id["r01c02"].spillover
    assert by_id["r01c03"].spillover and not by_id["r01c03"].inside_polygon


def test_negligible_change_is_dropped_rather_than_called_slight_cooling(
    synthetic_runtime: Runtime,
) -> None:
    window, label, grid = _setup(synthetic_runtime)

    mask = _mask(grid, slice(20, 40), slice(40, 60))
    delta = np.zeros(grid.shape, dtype="float32")
    # Below the hindcast-corrected threshold, so it rounds to nothing.
    delta[mask] = -NEGLIGIBLE_C

    assert (
        explain_pattern(
            window=window, label=label, grid=grid, delta=delta,
            canopy_added=np.where(mask, 0.3, 0.0), mask=mask,
        )
        == ()
    )


def test_regions_are_ordered_by_how_much_they_cooled(synthetic_runtime: Runtime) -> None:
    window, label, grid = _setup(synthetic_runtime)

    mask = np.zeros(grid.shape, dtype=bool)
    delta = np.zeros(grid.shape, dtype="float32")
    mask[20:40, 40:60] = True
    delta[20:40, 40:60] = -0.5
    mask[60:80, 40:60] = True
    delta[60:80, 40:60] = -2.0

    regions = explain_pattern(
        window=window, label=label, grid=grid, delta=delta,
        canopy_added=np.where(mask, 0.3, 0.0), mask=mask,
    )

    assert [region.region_id for region in regions] == ["r03c02", "r01c02"]
    assert regions[0].expected_cooling_c > regions[1].expected_cooling_c


def test_water_and_tree_cover_come_from_the_cube(synthetic_runtime: Runtime) -> None:
    """The synthetic cube puts a river down its six westernmost columns, so the westmost
    block reads as part water — which is what explains a block that refused to cool."""
    window, label, grid = _setup(synthetic_runtime)

    mask = _mask(grid, slice(20, 40), slice(0, 20))
    delta = np.zeros(grid.shape, dtype="float32")
    delta[mask] = -1.0

    regions = explain_pattern(
        window=window, label=label, grid=grid, delta=delta,
        canopy_added=np.where(mask, 0.3, 0.0), mask=mask,
    )

    assert regions[0].water_fraction > 0.2
    assert 0.0 <= regions[0].tree_cover_fraction <= 1.0


def test_the_table_carries_every_number_the_model_may_use(synthetic_runtime: Runtime) -> None:
    """`_numbers_are_faithful` compares the description against exactly this string, so a
    column missing from here is a figure the model is silently forbidden to mention."""
    window, label, grid = _setup(synthetic_runtime)

    mask = _mask(grid, slice(20, 40), slice(40, 60))
    delta = np.zeros(grid.shape, dtype="float32")
    delta[mask] = -1.0

    regions = explain_pattern(
        window=window, label=label, grid=grid, delta=delta,
        canopy_added=np.where(mask, 0.3, 0.0), mask=mask,
    )
    table = as_table(regions, label=label)

    assert "r01c02" in table
    assert "0.40" in table
    assert label in table
    assert "residents" in table and "spillover" in table
    assert "2.5x over-prediction" in table
