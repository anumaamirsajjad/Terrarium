"""Response contracts for the cube endpoints.

The one decision worth defending here is how a raster crosses the wire. The tile is
201 x 202 = 40,602 cells; as GeoJSON features that is tens of megabytes of coordinate
strings for a grid whose geometry is already known from three numbers. So a layer ships
as **base64 float32, row-major, plus bounds**, which is ~160 kB raw and is exactly what
deck.gl's BitmapLayer wants to sample.
"""

from __future__ import annotations

import base64

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from terrarium.state.cube import Dims, VariableSummary

# Little-endian float32, row-major, no padding. Named in the payload so a client never
# has to guess, and so changing it is a visible break rather than a silent corruption.
ARRAY_ENCODING = "base64:float32:little:row-major"


class GridInfo(BaseModel):
    """Where the raster sits, in both the analysis CRS and the map's."""

    model_config = ConfigDict(frozen=True)

    crs: str = Field(description="The analysis CRS the array is actually gridded in")
    resolution_m: int
    shape: tuple[int, int] = Field(description="(height, width) — rows then columns")
    bounds: tuple[float, float, float, float] = Field(
        description="[left, bottom, right, top] in the analysis CRS"
    )
    bounds_wgs84: tuple[float, float, float, float] = Field(
        description=(
            "[west, south, east, north] for map overlay. This is the *envelope* of the "
            "projected grid: the UTM rectangle is not a lat/lon rectangle, so a "
            "north-up overlay drawn from these bounds is off by a fraction of a cell at "
            "the corners. Acceptable for a 20 km tile, and stated rather than hidden."
        )
    )


class LayerResponse(BaseModel):
    """One cube variable as a raster the map can draw."""

    model_config = ConfigDict(frozen=True)

    variable: str
    description: str
    units: str
    # None for static variables — elevation does not belong to a window, and saying
    # "2024-summer" about it would imply a time variation that was never measured.
    window: str | None
    grid: GridInfo
    encoding: str = ARRAY_ENCODING
    data: str = Field(description="Base64 of the raw array bytes. NaN marks no-data.")
    # Range over valid cells only, so a client can build a colour ramp without decoding.
    vmin: float | None
    vmax: float | None
    valid_fraction: float


class CubeSummaryResponse(BaseModel):
    """What is in the served cube, and how trustworthy it looks."""

    model_config = ConfigDict(frozen=True)

    crs: str
    resolution_m: int
    shape: tuple[int, int]
    windows: list[str]
    default_window: str = Field(
        description="The window used when a request does not name one: the latest summer"
    )
    variables: list[VariableSummary]
    # Per-window valid share, so a client can tell a thin window from a full one rather
    # than trusting a whole-cube average that hides it.
    window_valid_fractions: dict[str, dict[str, float]]


def encode_array(values: np.ndarray) -> str:
    """Raw float32 bytes, base64. Row-major, NaN for no-data."""
    return base64.b64encode(np.ascontiguousarray(values, dtype="float32").tobytes()).decode()


def layer_window(dims: Dims, window: str) -> str | None:
    """The window to report for a variable with these dims — None if it is static."""
    return window if dims is Dims.TIME_SPACE else None
