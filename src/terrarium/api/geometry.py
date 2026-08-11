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


# No drawing library nests a Feature more than one deep inside a FeatureCollection, which
# is the whole reason `_as_geometry` unwraps at all - this bounds a body crafted to recurse
# the parser into a stack overflow (F20) well above that, with room to spare.
MAX_UNWRAP_DEPTH = 4


def _as_geometry(geojson: dict[str, Any], *, depth: int = 0) -> dict[str, Any]:
    """Unwrap a Feature or FeatureCollection down to a single geometry object.

    Drawing libraries hand back whichever of the three they feel like - MapLibre's draw
    plugin emits a Feature, some emit a bare geometry - so accepting only one of them
    would fail on the frontend's very first request for a reason nobody could see.
    """
    if depth > MAX_UNWRAP_DEPTH:
        raise GeometryError(
            f"geometry nests more than {MAX_UNWRAP_DEPTH} Feature/FeatureCollection "
            "layers deep, which no real drawing produces - refusing to unwrap further"
        )

    kind = geojson.get("type")

    if kind == "FeatureCollection":
        features = geojson.get("features") or []
        if len(features) != 1:
            raise GeometryError(
                f"expected exactly one feature, got {len(features)}. Draw one polygon, "
                "or send its geometry directly."
            )
        return _as_geometry(features[0], depth=depth + 1)

    if kind == "Feature":
        geometry = geojson.get("geometry")
        if not isinstance(geometry, dict):
            raise GeometryError("feature has no geometry")
        return _as_geometry(geometry, depth=depth + 1)

    if kind not in SUPPORTED_TYPES:
        raise GeometryError(
            f"geometry type {kind!r} is not supported; use one of {sorted(SUPPORTED_TYPES)}. "
            "An intervention needs an area, so points and lines cannot be used."
        )
    return geojson


# The mask is this tile's fixed cell count whatever the polygon's own resolution, so a
# finer polygon buys a client nothing (F21). A drawn polygon has dozens of vertices; ten
# thousand is generous headroom above that and well below what makes rasterising expensive.
MAX_VERTICES = 10_000


def _count_vertices(coordinates: Any) -> int:
    """Leaf coordinate pairs in a GeoJSON `coordinates` array, at any nesting depth.

    Polygon nests rings-of-pairs; MultiPolygon nests polygons-of-rings-of-pairs. Rather
    than special-case each, a `coordinates` list whose first element is a number *is* one
    pair; anything else is a list of smaller `coordinates` to recurse into.
    """
    if coordinates and isinstance(coordinates[0], (int, float)):
        return 1
    return sum(_count_vertices(item) for item in coordinates)


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

    vertices = _count_vertices(geometry.get("coordinates") or [])
    if vertices > MAX_VERTICES:
        raise GeometryError(
            f"geometry has {vertices:,} vertices; refusing above {MAX_VERTICES:,} - the "
            "mask this produces is the same fixed cell count either way, so a finer "
            "polygon does not buy a more precise answer"
        )

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
