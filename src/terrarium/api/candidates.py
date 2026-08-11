"""The candidate lattice: the only geometry the search agent is allowed to choose from.

Deterministic, and there is no model anywhere in this file. It lives in `api/` for exactly
the reason `measure.py` does — this is the layer that owns the grid — and it exists for
the reason D26 gives: **the model never emits geometry, it selects from geometry the grid
layer generated.** A model asked for coordinates on a 201x202 grid hallucinates them and
spends the step budget on polygons that select no cells; a model asked to pick `r03c05`
cannot.

Two implementation points that are the difference between fast and unusable:

- **`effective_fraction` is computed once over a full-tile mask, then block-reduced.**
  Calling `measure_polygon` per candidate is ~110 full-grid passes for an answer that one
  pass already contains. The full-tile call returns per-cell headroom with water and
  no-data already zeroed, which is exactly what needs summing.
- **A candidate's mask is built from row/column slices**, never rasterised. The GeoJSON is
  for the client and for handing the winning region back to `/simulate`; it is the inverse
  of what `geometry.py` does, and the two agree because a block's corners sit on cell
  edges while `mask_from_geojson` tests cell *centres* — 50 m of slack in every direction.

Regions are **mergeable**: every function here takes a sequence of candidates, so the agent
can grow a region rather than being stuck at 2 km granularity.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import xarray as xr
from pyproj import Transformer

from terrarium.agent.state import Candidate
from terrarium.cores.air import EMISSION_VARIABLE
from terrarium.cores.base import Intervention
from terrarium.cores.thermal.features import BASE_VARIABLES
from terrarium.cores.thermal.simulate import effective_fraction
from terrarium.dsl.schema import TREE_CANOPY_M2
from terrarium.dsl.validate import PolygonMeasurement
from terrarium.state.grid import Grid

WGS84 = "EPSG:4326"

# 20 cells at 100 m = 2 km x 2 km, which over the Lahore tile is 11 x 11 = 121 blocks
# (the eastern and southern edges are short — 201 and 202 do not divide by 20). Chosen
# because it is the scale a neighbourhood intervention is actually proposed at: 1 km
# blocks quadruple the prompt for regions too small to hold a plan worth costing, and
# 4 km blocks give the agent almost nothing to choose between.
BLOCK_CELLS = 20


def _region_id(block_row: int, block_col: int) -> str:
    return f"r{block_row:02d}c{block_col:02d}"


def _edges(length: int, step: int) -> list[int]:
    """Block start indices along one axis, including a short final block."""
    return list(range(0, length, step))


def _block_sum(values: np.ndarray, rows: list[int], cols: list[int]) -> np.ndarray:
    """Sum `values` into blocks. NaN counts as zero, which is what every caller wants.

    `population` and `pm25_emission_g_s` are extensive — a NaN cell holds no people and
    emits nothing, so treating it as zero is the honest reading rather than a convenience.
    `lst_c` is handled separately, by dividing a nan-sum by a finite-count, because a mean
    over a block that is half no-data must not be a mean over the whole block.
    """
    filled = np.nan_to_num(np.asarray(values, dtype="float64"), nan=0.0)
    return np.add.reduceat(np.add.reduceat(filled, rows, axis=0), cols, axis=1)


def build_lattice(
    window: xr.Dataset, grid: Grid, *, block_cells: int = BLOCK_CELLS
) -> tuple[Candidate, ...]:
    """Tile the grid into blocks and describe each one from the cube.

    Raises `ValueError` when the window has no tree-cover pixels to reference — the same
    failure `simulate` and `measure_polygon` raise, surfaced here so a search is refused
    before a core runs rather than half way through one.
    """
    height, width = grid.shape
    rows = _edges(height, block_cells)
    cols = _edges(width, block_cells)

    arrays = {name: np.asarray(window[name].values) for name in BASE_VARIABLES}
    # One full-tile pass. Asking for a canopy fraction of 1.0 makes the per-cell cap return
    # the headroom itself, with water and no-data already zeroed — the same trick
    # `api/measure.py` uses, for the same reason: the DSL and the physics must not keep
    # separate opinions about how green a cell is.
    headroom = effective_fraction(
        arrays, Intervention(mask=np.ones(grid.shape, dtype=bool), canopy_fraction_added=1.0)
    )

    cell_m2 = float(grid.resolution_m) ** 2
    canopy_m2 = _block_sum(headroom, rows, cols) * cell_m2

    lst = np.asarray(window["lst_c"].values, dtype="float64")
    finite = np.isfinite(lst)
    lst_total = _block_sum(np.where(finite, lst, 0.0), rows, cols)
    lst_count = _block_sum(finite.astype("float64"), rows, cols)

    population = (
        _block_sum(window["population"].values, rows, cols)
        if "population" in window
        else np.zeros_like(canopy_m2)
    )
    emissions = (
        _block_sum(window[EMISSION_VARIABLE].values, rows, cols)
        if EMISSION_VARIABLE in window
        else np.zeros_like(canopy_m2)
    )

    to_wgs84 = Transformer.from_crs(grid.crs, WGS84, always_xy=True)

    candidates: list[Candidate] = []
    for block_row, row0 in enumerate(rows):
        row1 = min(row0 + block_cells, height)
        for block_col, col0 in enumerate(cols):
            col1 = min(col0 + block_cells, width)
            cells = (row1 - row0) * (col1 - col0)
            count = float(lst_count[block_row, block_col])
            plantable = float(canopy_m2[block_row, block_col])

            candidates.append(
                Candidate(
                    region_id=_region_id(block_row, block_col),
                    row0=row0,
                    row1=row1,
                    col0=col0,
                    col1=col1,
                    cells=cells,
                    area_m2=cells * cell_m2,
                    plantable_canopy_m2=plantable,
                    max_trees=int(plantable // TREE_CANOPY_M2),
                    mean_lst_c=(float(lst_total[block_row, block_col]) / count if count else None),
                    population=float(population[block_row, block_col]),
                    emission_g_s=float(emissions[block_row, block_col]),
                    geometry=_block_geometry(row0, row1, col0, col1, grid, to_wgs84),
                )
            )

    return tuple(candidates)


def _block_geometry(
    row0: int, row1: int, col0: int, col1: int, grid: Grid, to_wgs84: Transformer
) -> dict[str, Any]:
    """One block as a WGS84 GeoJSON polygon.

    Four corners, not a densified edge. A projected rectangle's edges are very slightly
    curved in lon/lat, but over 2 km the sagitta is well under a metre and cell centres
    sit 50 m inside the boundary — so rasterising this back through `mask_from_geojson`
    reproduces the same cells the slices select. That equivalence is what lets the winning
    region be handed to `/simulate` unchanged.
    """
    left, _, _, top = grid.bounds
    res = grid.resolution_m
    x0, x1 = left + col0 * res, left + col1 * res
    y0, y1 = top - row1 * res, top - row0 * res

    lons, lats = to_wgs84.transform([x0, x1, x1, x0, x0], [y0, y0, y1, y1, y0])
    return {
        "type": "Polygon",
        "coordinates": [[[float(lon), float(lat)] for lon, lat in zip(lons, lats, strict=True)]],
    }


def region_mask(candidates: Sequence[Candidate], grid: Grid) -> np.ndarray:
    """The union of these blocks, as a boolean mask on the canonical grid.

    Slices rather than rasterisation. Passing more than one candidate is how a region gets
    grown past 2 km, and because the union is a plain OR the blocks need not be adjacent —
    an agent may propose two separated neighbourhoods as one intervention.
    """
    if not candidates:
        raise ValueError("no regions selected")

    mask = np.zeros(grid.shape, dtype=bool)
    for candidate in candidates:
        mask[candidate.row0 : candidate.row1, candidate.col0 : candidate.col1] = True
    return mask


def region_geometry(candidates: Sequence[Candidate]) -> dict[str, Any]:
    """The union as GeoJSON: a Polygon for one block, a MultiPolygon for several.

    Not dissolved. Adjacent blocks stay separate rings, which `mask_from_geojson` handles
    (`MultiPolygon` is in its supported set) and which keeps this function free of a
    geometry library it would otherwise need for one cosmetic union.
    """
    if not candidates:
        raise ValueError("no regions selected")
    if len(candidates) == 1:
        return candidates[0].geometry

    return {
        "type": "MultiPolygon",
        "coordinates": [candidate.geometry["coordinates"] for candidate in candidates],
    }


def region_measurement(candidates: Sequence[Candidate]) -> PolygonMeasurement:
    """What `dsl.validate.resolve` needs, summed straight off the lattice.

    The blocks are disjoint by construction, so summing is exact and no second grid pass
    is needed. This is what makes the refusal loop cheap: a proposal is checked against
    the tile's real headroom in microseconds, and only a proposal that survives costs a
    simulation.
    """
    if not candidates:
        raise ValueError("no regions selected")

    return PolygonMeasurement(
        cells=sum(candidate.cells for candidate in candidates),
        area_m2=sum(candidate.area_m2 for candidate in candidates),
        plantable_canopy_m2=sum(candidate.plantable_canopy_m2 for candidate in candidates),
    )
