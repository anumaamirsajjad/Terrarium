"""The lattice has one property that everything downstream depends on: the mask built
from row/column slices and the mask rasterised from the GeoJSON must be the same cells.

If they diverge, the agent scores one region and hands the user a different one — a bug
that would show up as "the applied plan does not reproduce the search result" and would be
extremely hard to trace back to a projection round-trip.
"""

from __future__ import annotations

import numpy as np
import pytest
import xarray as xr

from terrarium.agent.state import Candidate
from terrarium.api.candidates import (
    BLOCK_CELLS,
    build_lattice,
    region_geometry,
    region_mask,
    region_measurement,
)
from terrarium.api.geometry import mask_from_geojson
from terrarium.api.measure import measure_polygon
from terrarium.api.runtime import Runtime
from terrarium.state.cube import select_window


def _lattice(synthetic_runtime: Runtime) -> tuple[xr.Dataset, tuple[Candidate, ...]]:
    window = select_window(synthetic_runtime.cube, synthetic_runtime.default_window())
    return window, build_lattice(window, synthetic_runtime.grid)


def test_lattice_covers_every_cell_exactly_once(synthetic_runtime: Runtime) -> None:
    _, candidates = _lattice(synthetic_runtime)
    grid = synthetic_runtime.grid

    coverage = np.zeros(grid.shape, dtype=int)
    for candidate in candidates:
        coverage[candidate.row0 : candidate.row1, candidate.col0 : candidate.col1] += 1

    assert coverage.min() == 1 and coverage.max() == 1
    assert sum(c.cells for c in candidates) == grid.shape[0] * grid.shape[1]


def test_geometry_rasterises_back_to_the_same_cells(synthetic_runtime: Runtime) -> None:
    """The round trip that D26 rests on: UTM slices -> WGS84 -> rasterised mask."""
    _, candidates = _lattice(synthetic_runtime)
    grid = synthetic_runtime.grid

    # An interior block, so neither edge clipping nor the short final block is in play.
    interior = [c for c in candidates if c.cells == BLOCK_CELLS**2][5]

    assert np.array_equal(
        region_mask([interior], grid), mask_from_geojson(interior.geometry, grid)
    )


def test_merged_regions_rasterise_back_too(synthetic_runtime: Runtime) -> None:
    _, candidates = _lattice(synthetic_runtime)
    grid = synthetic_runtime.grid

    full = [c for c in candidates if c.cells == BLOCK_CELLS**2]
    pair = [full[5], full[9]]

    assert np.array_equal(
        region_mask(pair, grid), mask_from_geojson(region_geometry(pair), grid)
    )


def test_block_measurement_matches_measure_polygon(synthetic_runtime: Runtime) -> None:
    """The lattice's summed headroom is the same number `dsl.validate` would be handed.

    Summing per block rather than calling `measure_polygon` 121 times is the optimisation
    that makes the lattice affordable; this is the check that it is only an optimisation.
    """
    window, candidates = _lattice(synthetic_runtime)
    grid = synthetic_runtime.grid

    chosen = [c for c in candidates if c.plantable_canopy_m2 > 0][3]
    direct = measure_polygon(window, region_mask([chosen], grid), grid)
    summed = region_measurement([chosen])

    assert summed.cells == direct.cells
    assert summed.area_m2 == direct.area_m2
    # `approx` only for summation order: the block reduce adds the same cells in a
    # different sequence to `headroom.sum()`, which is worth a last-ulp difference and
    # nothing more. A real disagreement would be percent-scale.
    assert summed.plantable_canopy_m2 == pytest.approx(direct.plantable_canopy_m2, rel=1e-12)


def test_water_blocks_hold_nothing_plantable(synthetic_runtime: Runtime) -> None:
    """The synthetic cube puts a river down its six westernmost columns.

    Those cells contribute zero headroom, so the western column of blocks must offer
    strictly less than its neighbour — the property that stops the agent proposing a
    planting on the Ravi and being refused for it ten steps running.
    """
    _, candidates = _lattice(synthetic_runtime)
    by_id = {c.region_id: c for c in candidates}

    west = by_id["r05c00"]
    inland = by_id["r05c01"]
    assert west.plantable_canopy_m2 < inland.plantable_canopy_m2


def test_population_is_summed_not_averaged(synthetic_runtime: Runtime) -> None:
    """Population is extensive. A block's figure is a head count, so the lattice total
    must equal the tile total — averaging would silently destroy most of the residents."""
    _, candidates = _lattice(synthetic_runtime)
    total = float(np.nan_to_num(synthetic_runtime.cube["population"].values).sum())

    assert sum(c.population for c in candidates) == float(total)
