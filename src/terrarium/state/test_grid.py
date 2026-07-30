"""Tests for the canonical grid.

The grid is the contract every other layer is measured against, so these guard the two
things that would silently corrupt everything downstream: the wrong pixel size, and
coordinates that drift between builds.
"""

from __future__ import annotations

import numpy as np

from terrarium.config import ACTIVE_TILE, Tile
from terrarium.state.grid import Grid, grid_for_tile


def test_resolution_comes_from_the_tile() -> None:
    grid = grid_for_tile(ACTIVE_TILE)
    assert grid.resolution_m == ACTIVE_TILE.target_resolution_m == 100


def test_shape_matches_100m_over_the_bbox() -> None:
    """~20 km at 100 m is ~200 px a side. Guards against a silent unit slip."""
    grid = grid_for_tile(ACTIVE_TILE)
    height, width = grid.shape

    assert 190 <= height <= 215
    assert 190 <= width <= 215


def test_bounds_snap_to_whole_pixels() -> None:
    grid = grid_for_tile(ACTIVE_TILE)
    for bound in grid.bounds:
        assert bound % grid.resolution_m == 0


def test_grid_covers_the_whole_requested_bbox() -> None:
    """Snapping must round outward, never clip the requested extent."""
    from pyproj import Transformer

    tile = ACTIVE_TILE
    west, south, east, north = tile.bbox
    transformer = Transformer.from_crs("EPSG:4326", tile.crs, always_xy=True)
    xs, ys = transformer.transform([west, east, east, west], [south, south, north, north])

    left, bottom, right, top = grid_for_tile(tile).bounds
    assert left <= min(xs)
    assert bottom <= min(ys)
    assert right >= max(xs)
    assert top >= max(ys)


def test_coords_are_pixel_centres_and_y_descends() -> None:
    grid = grid_for_tile(ACTIVE_TILE)
    left, _, _, top = grid.bounds
    half = grid.resolution_m / 2

    x = grid.x_coords()
    y = grid.y_coords()

    assert x[0] == left + half
    assert y[0] == top - half
    assert np.all(np.diff(x) > 0)
    assert np.all(np.diff(y) < 0)
    assert len(x) == grid.width
    assert len(y) == grid.height


def test_grid_is_deterministic() -> None:
    assert grid_for_tile(ACTIVE_TILE) == grid_for_tile(ACTIVE_TILE)


def test_changing_target_resolution_changes_the_shape() -> None:
    """The one field that must actually drive the output grid."""
    coarse = grid_for_tile(ACTIVE_TILE.model_copy(update={"target_resolution_m": 200}))
    fine = grid_for_tile(ACTIVE_TILE.model_copy(update={"target_resolution_m": 100}))

    assert coarse.height * 2 == fine.height or abs(coarse.height * 2 - fine.height) <= 1
    assert coarse.resolution_m == 200


def test_empty_has_grid_shape_and_coords() -> None:
    grid = Grid(crs="EPSG:32643", resolution_m=100, bounds=(0.0, 0.0, 500.0, 300.0))
    empty = grid.empty()

    assert grid.shape == (3, 5)
    assert empty.dims == ("y", "x")
    assert empty.shape == (3, 5)
    assert np.isnan(empty.values).all()


def test_transform_is_north_up() -> None:
    grid = Grid(crs="EPSG:32643", resolution_m=100, bounds=(1000.0, 2000.0, 1500.0, 2300.0))
    a, b, c, d, e, f = grid.transform

    assert (a, b, c) == (100.0, 0.0, 1000.0)
    assert (d, e, f) == (0.0, -100.0, 2300.0)


def test_tile_centroid() -> None:
    tile = Tile(name="T", country="XX", bbox=(0.0, 0.0, 2.0, 4.0), crs="EPSG:32643")
    assert tile.centroid == (1.0, 2.0)
