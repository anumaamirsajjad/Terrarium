"""GeoJSON -> mask conversion. Offline: nothing here touches the network."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from terrarium.api.geometry import GeometryError, cell_from_lonlat, mask_from_geojson
from terrarium.config import ACTIVE_TILE
from terrarium.state.grid import Grid, grid_for_tile

# A ~1 km box near the middle of the Lahore tile.
CENTRE_LON, CENTRE_LAT = 74.3587, 31.5204
HALF_DEG = 0.005  # ~550 m


@pytest.fixture
def grid() -> Grid:
    return grid_for_tile(ACTIVE_TILE)


def box(lon: float, lat: float, half: float = HALF_DEG) -> dict[str, Any]:
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [lon - half, lat - half],
                [lon + half, lat - half],
                [lon + half, lat + half],
                [lon - half, lat + half],
                [lon - half, lat - half],
            ]
        ],
    }


def test_a_polygon_becomes_a_mask_of_the_right_shape(grid: Grid) -> None:
    mask = mask_from_geojson(box(CENTRE_LON, CENTRE_LAT), grid)

    assert mask.shape == grid.shape
    assert mask.dtype == bool
    assert mask.any()


def test_the_mask_lands_where_the_polygon_is(grid: Grid) -> None:
    """Not just "some cells" - the right cells.

    A conversion that silently mirrored an axis or dropped the projection would still
    return a plausible-looking mask, so check the selected cells' centroid against the
    polygon's own centre in projected coordinates.
    """
    from pyproj import Transformer

    mask = mask_from_geojson(box(CENTRE_LON, CENTRE_LAT), grid)

    ys, xs = np.nonzero(mask)
    got_x = float(grid.x_coords()[xs].mean())
    got_y = float(grid.y_coords()[ys].mean())

    want_x, want_y = Transformer.from_crs(
        "EPSG:4326", grid.crs, always_xy=True
    ).transform(CENTRE_LON, CENTRE_LAT)

    # Within one cell of the true centre.
    assert abs(got_x - want_x) < grid.resolution_m
    assert abs(got_y - want_y) < grid.resolution_m


def test_a_bigger_polygon_selects_more_cells(grid: Grid) -> None:
    small = mask_from_geojson(box(CENTRE_LON, CENTRE_LAT, 0.003), grid)
    large = mask_from_geojson(box(CENTRE_LON, CENTRE_LAT, 0.010), grid)

    assert large.sum() > small.sum()
    # The small polygon is wholly inside the large one, so its cells must be too.
    assert not (small & ~large).any()


def test_a_feature_is_unwrapped(grid: Grid) -> None:
    feature = {"type": "Feature", "properties": {}, "geometry": box(CENTRE_LON, CENTRE_LAT)}
    bare = mask_from_geojson(box(CENTRE_LON, CENTRE_LAT), grid)

    assert np.array_equal(mask_from_geojson(feature, grid), bare)


def test_a_single_feature_collection_is_unwrapped(grid: Grid) -> None:
    collection = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {}, "geometry": box(CENTRE_LON, CENTRE_LAT)}
        ],
    }
    bare = mask_from_geojson(box(CENTRE_LON, CENTRE_LAT), grid)

    assert np.array_equal(mask_from_geojson(collection, grid), bare)


def test_a_multi_feature_collection_is_rejected(grid: Grid) -> None:
    collection = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {}, "geometry": box(CENTRE_LON, CENTRE_LAT)},
            {"type": "Feature", "properties": {}, "geometry": box(CENTRE_LON, CENTRE_LAT)},
        ],
    }
    with pytest.raises(GeometryError, match="exactly one feature"):
        mask_from_geojson(collection, grid)


@pytest.mark.parametrize(
    "geometry",
    [
        {"type": "Point", "coordinates": [CENTRE_LON, CENTRE_LAT]},
        {"type": "LineString", "coordinates": [[74.30, 31.50], [74.40, 31.55]]},
    ],
)
def test_geometry_without_area_is_rejected(grid: Grid, geometry: dict[str, Any]) -> None:
    """A point or a line rasterises to nothing, which would read as "no effect"."""
    with pytest.raises(GeometryError, match="not supported"):
        mask_from_geojson(geometry, grid)


def test_a_polygon_outside_the_tile_is_rejected_not_silently_empty(grid: Grid) -> None:
    """The most dangerous failure mode: an all-False mask simulates cleanly to zero.

    A zero delta everywhere looks exactly like a working model that found no effect, so
    this has to be an error rather than a result.
    """
    with pytest.raises(GeometryError, match="selects no grid cells"):
        mask_from_geojson(box(0.0, 0.0), grid)  # Gulf of Guinea


def test_a_self_intersecting_polygon_is_rejected(grid: Grid) -> None:
    bowtie = {
        "type": "Polygon",
        "coordinates": [
            [
                [74.35, 31.51],
                [74.37, 31.53],
                [74.35, 31.53],
                [74.37, 31.51],
                [74.35, 31.51],
            ]
        ],
    }
    with pytest.raises(GeometryError, match="not valid"):
        mask_from_geojson(bowtie, grid)


def test_a_multipolygon_selects_both_lobes(grid: Grid) -> None:
    west = box(74.30, 31.50, 0.004)
    east = box(74.42, 31.55, 0.004)
    multi = {
        "type": "MultiPolygon",
        "coordinates": [west["coordinates"], east["coordinates"]],
    }

    mask = mask_from_geojson(multi, grid)
    expected = mask_from_geojson(west, grid) | mask_from_geojson(east, grid)

    assert np.array_equal(mask, expected)
    assert mask.sum() == expected.sum() > 0


# ------------------------------------------------------------------------------------
# cell_from_lonlat: the point-shaped path, used by POST /observations
# ------------------------------------------------------------------------------------


def test_a_point_in_the_middle_lands_in_a_real_cell(grid: Grid) -> None:
    row, col = cell_from_lonlat(CENTRE_LON, CENTRE_LAT, grid)
    assert 0 <= row < grid.shape[0]
    assert 0 <= col < grid.shape[1]


def test_a_point_outside_the_tile_is_refused(grid: Grid) -> None:
    # Karachi. Clamping to the nearest edge cell would be a wrong answer wearing a
    # plausible one's clothes.
    with pytest.raises(GeometryError, match="outside the tile"):
        cell_from_lonlat(67.0, 24.86, grid)


@pytest.mark.parametrize("corner", ["nw", "ne", "sw", "se"])
def test_every_accepted_edge_coordinate_maps_to_a_real_cell(grid: Grid, corner: str) -> None:
    """The tile's own bounds, half-open the way the row/col arithmetic is.

    A north-up grid counts rows down from `top`, so `top` is row 0 and `bottom` is one row
    past the last. The check used to accept `y == bottom` and reject `y == top`, which had
    both edges the wrong way round: a photo on the northern boundary was refused, and one
    on the southern boundary was accepted and then returned row `shape[0]` — an index error
    on a coordinate the check had just called valid.
    """
    from pyproj import Transformer

    from terrarium.api.geometry import WGS84

    left, bottom, right, top = grid.bounds
    x = left if corner in ("nw", "sw") else right
    y = top if corner in ("nw", "ne") else bottom
    lon, lat = Transformer.from_crs(grid.crs, WGS84, always_xy=True).transform(x, y)

    try:
        row, col = cell_from_lonlat(lon, lat, grid)
    except GeometryError:
        return  # refusing a boundary is fine; returning a bad index is not
    assert 0 <= row < grid.shape[0], f"{corner}: row {row} outside {grid.shape}"
    assert 0 <= col < grid.shape[1], f"{corner}: col {col} outside {grid.shape}"


def test_the_north_west_corner_is_the_first_cell(grid: Grid) -> None:
    """`top`/`left` are inclusive, and they are cell (0, 0) — not a refusal, not row 201."""
    from pyproj import Transformer

    from terrarium.api.geometry import WGS84

    left, _bottom, _right, top = grid.bounds
    lon, lat = Transformer.from_crs(grid.crs, WGS84, always_xy=True).transform(left, top)
    assert cell_from_lonlat(lon, lat, grid) == (0, 0)
