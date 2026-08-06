"""The contract every physics core implements.

A core is a pure function `core(cube, intervention, model) -> CoreResult`. The trained
model arrives as an *argument*: training per call would kill the interactivity claim, and
loading it from disk would break the purity rule that makes cores testable offline.

This module holds no physics. It exists so the API layer and the cores agree on a shape
without either importing the other's internals.
"""

from __future__ import annotations

from typing import Protocol

import numpy as np
import xarray as xr
from pydantic import BaseModel, ConfigDict, Field
from scipy.ndimage import binary_dilation

# How far outside the intervention still counts as spillover, in grid cells. Matches the
# radius of the thermal core's 5x5 neighbourhood feature: past that the delta is exactly
# zero, so a wider ring only dilutes the number with cells that cannot have changed.
SPILLOVER_CELLS = 2


class Intervention(BaseModel):
    """What the user asked for, in terms a core can act on.

    Geometry is already resolved to a boolean grid mask (D6): converting GeoJSON to a
    mask is the API's job, because it needs the grid, and a core is not allowed to know
    where the grid came from.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    # (y, x) boolean. True where the intervention applies.
    mask: np.ndarray
    # Canopy cover to *add*, as a fraction of cell area. Capped per cell against what is
    # actually still plantable there - see cores.thermal.simulate.
    canopy_fraction_added: float = Field(ge=0.0, le=1.0)
    # Share of the cell's PM2.5 emissions removed inside the mask - a low-emission zone.
    # Read only by the air core; the thermal core has no mechanism for it, and adding one
    # would be inventing a link between traffic and surface temperature that the emulator
    # was never trained on. One `Intervention` describes one plan, and a plan may say
    # nothing about traffic, which is what the 0.0 default means (D14).
    emission_fraction_removed: float = Field(default=0.0, ge=0.0, le=1.0)


class DeltaStats(BaseModel):
    """Scalar summary of a delta field. What a UI panel shows without the raster."""

    model_config = ConfigDict(frozen=True)

    n_cells_changed: int
    mean_delta_inside: float
    # Mean delta in the ring just outside the intervention - the spillover. Deliberately
    # NOT "everything not planted": the effect decays to exactly zero past the feature
    # neighbourhood, so averaging over the whole tile divides a real local effect by
    # 40,000 untouched cells and reports ~0.000, which reads as "no spillover" on a panel
    # when the ring is actually cooling by an order of magnitude more.
    mean_delta_spillover: float
    spillover_cells: int
    # Strongest cooling anywhere in the tile (most negative) and the largest warming.
    min_delta: float
    max_delta: float


class CoreResult(BaseModel):
    """A core's answer: one delta field on the analysis grid, plus its summary.

    `delta` is scenario *minus* baseline, both predicted by the same model. Never
    prediction-minus-observation: that leaks the model's residual into every scenario and
    paints spurious change onto pixels nobody touched.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    variable: str
    units: str
    # (y, x) float32, NaN where the baseline had no usable inputs.
    delta: np.ndarray
    stats: DeltaStats


class Core(Protocol):
    """Structural type every simulator satisfies."""

    def __call__(
        self, cube: xr.Dataset, intervention: Intervention, model: object
    ) -> CoreResult: ...


def summarise_delta(
    delta: np.ndarray, mask: np.ndarray, *, spillover_cells: int = SPILLOVER_CELLS
) -> DeltaStats:
    """Reduce a delta field to the scalars a panel needs. NaN cells are ignored."""
    finite = np.isfinite(delta)
    inside = finite & mask
    ring = finite & binary_dilation(mask, iterations=spillover_cells) & ~mask

    return DeltaStats(
        n_cells_changed=int(mask.sum()),
        mean_delta_inside=float(delta[inside].mean()) if inside.any() else 0.0,
        mean_delta_spillover=float(delta[ring].mean()) if ring.any() else 0.0,
        spillover_cells=int(ring.sum()),
        min_delta=float(delta[finite].min()) if finite.any() else 0.0,
        max_delta=float(delta[finite].max()) if finite.any() else 0.0,
    )
