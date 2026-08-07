"""GeoJSON polygon -> boolean grid mask.

This is the API's job and nowhere else's (D6). A core takes a `(y, x)` boolean mask on
the canonical grid and is not allowed to know where the grid came from; the frontend
draws in WGS84 lon/lat and does not know the projection. This module is the one place
that speaks both.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from pyproj import Transformer
from rasterio.features import rasterize
from rasterio.transform import Affine
from shapely.geometry import mapping, shape
from shapely.ops import transform as shapely_transform

from terrarium.state.grid import Grid

# What a user can draw. Points and lines have no area, so they would rasterise to an
# empty mask and silently return a zero delta rather than an error.
SUPPORTED_TYPES = frozenset({"Polygon", "MultiPolygon"})

WGS84 = "EPSG:4326"


class GeometryError(ValueError):
    """The submitted geometry cannot become a mask. Carries a user-facing reason."""


def _as_geometry(geojson: dict[str, Any]) -> dict[str, Any]:
    """Unwrap a Feature or FeatureCollection down to a single geometry object.

    Drawing libraries hand back whichever of the three they feel like - MapLibre's draw
    plugin emits a Feature, some emit a bare geometry - so accepting only one of them
    would fail on the frontend's very first request for a reason nobody could see.
    """
    kind = geojson.get("type")

    if kind == "FeatureCollection":
        features = geojson.get("features") or []
        if len(features) != 1:
            raise GeometryError(
                f"expected exactly one feature, got {len(features)}. Draw one polygon, "
                "or send its geometry directly."
            )
        return _as_geometry(features[0])

    if kind == "Feature":
        geometry = geojson.get("geometry")
        if not isinstance(geometry, dict):
            raise GeometryError("feature has no geometry")
        return _as_geometry(geometry)

    if kind not in SUPPORTED_TYPES:
        raise GeometryError(
            f"geometry type {kind!r} is not supported; use one of {sorted(SUPPORTED_TYPES)}. "
            "An intervention needs an area, so points and lines cannot be used."
        )
    return geojson


def cell_from_lonlat(lon: float, lat: float, grid: Grid) -> tuple[int, int]:
    """Which grid cell a WGS84 point falls in, as `(row, col)`.

    The point-shaped counterpart to `mask_from_geojson`, and it lives here for the same
    reason: this is the one module that speaks both WGS84 and the analysis CRS.

    Raises `GeometryError` outside the tile. Clamping instead would silently pin a photo
    taken in another city to the nearest edge cell, which is a wrong answer wearing a
    plausible one's clothes.
    """
    to_grid = Transformer.from_crs(WGS84, grid.crs, always_xy=True)
    x, y = to_grid.transform(lon, lat)

    left, bottom, right, top = grid.bounds
    # Half-open the way the row/col arithmetic below is: a north-up grid counts rows down
    # from `top`, so `top` itself is row 0 and `bottom` is one row past the last. Accepting
    # `y == bottom` (and rejecting `y == top`) had both edges the wrong way round and
    # returned row `shape[0]` - an index error on a coordinate the check had just called
    # valid. x is already correct, because col counts up from `left`.
    if not (left <= x < right and bottom < y <= top):
        raise GeometryError(
            f"({lon:.4f}, {lat:.4f}) is outside the tile. Terrarium models one tile "
            "(Lahore) and has nothing to say about anywhere else."
        )

    return (int((top - y) // grid.resolution_m), int((x - left) // grid.resolution_m))


def mask_from_geojson(geojson: dict[str, Any], grid: Grid) -> np.ndarray:
    """Rasterise a WGS84 GeoJSON polygon onto the canonical grid.

    A cell is inside when its **centre** falls within the polygon, which is rasterio's
    default and the right rule here: the alternative (any touched cell) would inflate a
    small polygon by a ring of cells the user did not draw, and at 100 m that ring is a
    meaningful share of a neighbourhood-scale intervention.

    Raises `GeometryError` - never returns an all-False mask. A mask of nothing produces
    a delta of exactly zero everywhere, which looks like a working simulation that found
    no effect rather than a polygon in the wrong place.
    """
    geometry = _as_geometry(geojson)

    try:
        geom = shape(geometry)
    except Exception as exc:  # shapely raises several unrelated types on bad input
        raise GeometryError(f"could not read geometry: {exc}") from exc

    if geom.is_empty:
        raise GeometryError("geometry is empty")
    if not geom.is_valid:
        # Self-intersecting polygons rasterise to something arbitrary rather than
        # failing, so reject rather than guess which lobe was meant.
        raise GeometryError(
            "geometry is not valid (it probably self-intersects); redraw it without "
            "crossing its own edge"
        )

    to_grid = Transformer.from_crs(WGS84, grid.crs, always_xy=True).transform
    projected = shapely_transform(to_grid, geom)

    raster = rasterize(
        [(mapping(projected), 1)],
        out_shape=grid.shape,
        transform=Affine(*grid.transform),
        fill=0,
        dtype="uint8",
        all_touched=False,
    )
    mask = np.asarray(raster, dtype=bool)

    if not mask.any():
        raise GeometryError(
            "the polygon selects no grid cells - it is outside the tile, or smaller "
            f"than one {grid.resolution_m} m cell"
        )
    return mask
