"""The canonical analysis grid.

Exactly one grid exists. Every ingested source is resampled onto it, and every core
assumes it. The grid is derived from `config.ACTIVE_TILE` — bbox reprojected into the
tile's metric CRS, then snapped outward to whole multiples of the target resolution so
pixel edges land on round coordinates and repeated builds are byte-identical.
"""

from __future__ import annotations

import math

import numpy as np
import xarray as xr
from pydantic import BaseModel, Field
from pyproj import Transformer

from terrarium.config import ACTIVE_TILE, Tile


class Grid(BaseModel):
    """An immutable description of the analysis raster.

    Coordinates follow the raster convention: `x` ascends left to right, `y` *descends*
    top to bottom, and each coordinate is the *centre* of its pixel.
    """

    model_config = {"frozen": True}

    crs: str
    resolution_m: int = Field(gt=0, description="Pixel size, metres. Square pixels only.")
    # Projected bounds, snapped to resolution multiples: [left, bottom, right, top].
    bounds: tuple[float, float, float, float]

    @property
    def width(self) -> int:
        left, _, right, _ = self.bounds
        return round((right - left) / self.resolution_m)

    @property
    def height(self) -> int:
        _, bottom, _, top = self.bounds
        return round((top - bottom) / self.resolution_m)

    @property
    def shape(self) -> tuple[int, int]:
        """(y, x) — matching xarray dimension order."""
        return (self.height, self.width)

    @property
    def transform(self) -> tuple[float, float, float, float, float, float]:
        """Affine coefficients (a, b, c, d, e, f) in rasterio's GDAL-ish order."""
        left, _, _, top = self.bounds
        return (float(self.resolution_m), 0.0, left, 0.0, -float(self.resolution_m), top)

    def x_coords(self) -> np.ndarray:
        left, _, _, _ = self.bounds
        half = self.resolution_m / 2
        return left + half + np.arange(self.width, dtype="float64") * self.resolution_m

    def y_coords(self) -> np.ndarray:
        _, _, _, top = self.bounds
        half = self.resolution_m / 2
        return top - half - np.arange(self.height, dtype="float64") * self.resolution_m

    def coords(self) -> dict[str, np.ndarray]:
        """Coordinate mapping ready to hand to `xr.Dataset`."""
        return {"y": self.y_coords(), "x": self.x_coords()}

    def empty(self, fill: float = np.nan, dtype: str = "float32") -> xr.DataArray:
        """A correctly-shaped, correctly-georeferenced array of `fill`.

        Used for variables that were searched for but came back with no usable data, so
        the cube's variable list stays stable whether or not a source was available.
        """
        data = np.full(self.shape, fill, dtype=dtype)
        return xr.DataArray(data, dims=("y", "x"), coords=self.coords())


def _snap_out(value: float, step: int, *, up: bool) -> float:
    """Round `value` outward to the nearest multiple of `step`."""
    return float(math.ceil(value / step) * step if up else math.floor(value / step) * step)


def grid_for_tile(tile: Tile = ACTIVE_TILE) -> Grid:
    """Build the canonical grid for a tile.

    The WGS84 bbox is a lat/lon rectangle; in UTM it is a slight quadrilateral, so we
    project all four corners and take their envelope rather than just two corners. That
    guarantees the grid covers the whole requested bbox instead of clipping the edges.
    """
    west, south, east, north = tile.bbox
    transformer = Transformer.from_crs("EPSG:4326", tile.crs, always_xy=True)

    corners_lon = [west, east, east, west]
    corners_lat = [south, south, north, north]
    xs, ys = transformer.transform(corners_lon, corners_lat)

    res = tile.target_resolution_m
    bounds = (
        _snap_out(min(xs), res, up=False),
        _snap_out(min(ys), res, up=False),
        _snap_out(max(xs), res, up=True),
        _snap_out(max(ys), res, up=True),
    )
    return Grid(crs=tile.crs, resolution_m=res, bounds=bounds)
