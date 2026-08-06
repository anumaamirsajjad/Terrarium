"""What the tile says about a drawn polygon, in the units the DSL validates against.

`dsl/` is pure arithmetic and has no cube, so somebody has to look up how much of a polygon
can actually take a tree before "5,000 trees" can be accepted or refused. That is this
module, and it lives in the API for the same reason the GeoJSON conversion does (D6): this
is the layer that owns the grid.

The headroom is not recomputed here from a rule of thumb. It is `cores.thermal.simulate`'s
own `effective_fraction`, asked for the maximum: request a canopy fraction of 1.0 and what
comes back per cell *is* the room that cell has left, with water and no-data already zeroed.
Using the core's function rather than a parallel one is what stops the DSL and the physics
disagreeing about how green a cell already is.
"""

from __future__ import annotations

import numpy as np
import xarray as xr

from terrarium.cores.base import Intervention
from terrarium.cores.thermal.features import BASE_VARIABLES
from terrarium.cores.thermal.simulate import effective_fraction
from terrarium.dsl.validate import PolygonMeasurement
from terrarium.state.grid import Grid

# Asking for all of it. The per-cell cap then returns the headroom itself.
FULL_CANOPY = 1.0


def measure_polygon(window: xr.Dataset, mask: np.ndarray, grid: Grid) -> PolygonMeasurement:
    """Area and remaining plantable canopy inside `mask`, in m2.

    Raises `ValueError` when the window has no tree-cover pixels to reference — the same
    failure `simulate` raises, surfaced here first so a plan is refused before a core runs.
    """
    arrays = {name: np.asarray(window[name].values) for name in BASE_VARIABLES}
    headroom = effective_fraction(
        arrays, Intervention(mask=mask, canopy_fraction_added=FULL_CANOPY)
    )

    cell_m2 = float(grid.resolution_m) ** 2
    cells = int(mask.sum())

    return PolygonMeasurement(
        cells=cells,
        area_m2=cells * cell_m2,
        # Summed over the whole grid rather than over the mask: `effective_fraction` is
        # already zero everywhere outside it, and summing the array is one pass instead of
        # an index that could quietly disagree with the core's own plantability rules.
        plantable_canopy_m2=float(headroom.sum()) * cell_m2,
    )
