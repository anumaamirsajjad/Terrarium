"""`preview_cube.py`: the geometry behind the PNGs, not the PNGs.

These renders are what a person checks alignment against, so a helper that mirrors an axis
or mislabels an extent produces a picture that looks fine and is wrong — which is worse
than no picture. The matplotlib drawing itself is not tested; the coordinate arithmetic
feeding it is.
"""

from __future__ import annotations

import numpy as np
import preview_cube
import pytest
import xarray as xr

from terrarium.config import ACTIVE_TILE
from terrarium.state.grid import grid_for_tile

GRID = grid_for_tile(ACTIVE_TILE)
WATER_CLASS = 80


def _lonlat_dataarray() -> xr.DataArray:
    return xr.DataArray(
        np.zeros((4, 5), dtype="float32"),
        dims=("y", "x"),
        coords={"y": [31.6, 31.55, 31.5, 31.45], "x": [74.26, 74.30, 74.34, 74.38, 74.42]},
    )


def test_the_extent_is_left_right_bottom_top() -> None:
    """matplotlib's `imshow` order. Swapping bottom and top flips the map vertically."""
    left, right, bottom, top = preview_cube._extent(_lonlat_dataarray())

    assert left < right
    assert bottom < top
    assert (left, right) == pytest.approx((74.26, 74.42))
    assert (bottom, top) == pytest.approx((31.45, 31.6))


def test_the_extent_does_not_depend_on_coordinate_order() -> None:
    """The cube's y descends and its x ascends; the extent must come out the same either way."""
    ascending = _lonlat_dataarray().sortby("y")

    assert preview_cube._extent(ascending) == pytest.approx(
        preview_cube._extent(_lonlat_dataarray())
    )


def _cube_with_water(mask: np.ndarray) -> xr.Dataset:
    landcover = np.where(mask, WATER_CLASS, 50).astype("uint8")
    return xr.Dataset(
        {"landcover": (("y", "x"), landcover)},
        coords={"y": GRID.y_coords(), "x": GRID.x_coords()},
    )


def test_water_pixels_come_back_as_lon_lat_inside_the_tile() -> None:
    mask = np.zeros(GRID.shape, dtype=bool)
    mask[:, :6] = True  # a river down the western edge, as the fixtures use

    found = preview_cube._water_lonlat(_cube_with_water(mask), GRID)

    assert found is not None
    lon, lat = found
    assert lon.size == lat.size == mask.sum()
    west, south, east, north = ACTIVE_TILE.bbox
    assert lon.min() >= west - 0.01 and lon.max() <= east + 0.01
    assert lat.min() >= south - 0.01 and lat.max() <= north + 0.01


def test_western_water_really_is_in_the_west() -> None:
    """The check that catches a row/column transposition, which stays plausible otherwise."""
    western = np.zeros(GRID.shape, dtype=bool)
    western[:, :6] = True
    eastern = np.zeros(GRID.shape, dtype=bool)
    eastern[:, -6:] = True

    west_lon, _ = preview_cube._water_lonlat(_cube_with_water(western), GRID)  # type: ignore[misc]
    east_lon, _ = preview_cube._water_lonlat(_cube_with_water(eastern), GRID)  # type: ignore[misc]

    assert west_lon.mean() < east_lon.mean()


def test_northern_water_really_is_in_the_north() -> None:
    """Row 0 is north on the canonical grid, and nothing in a picture would show otherwise."""
    northern = np.zeros(GRID.shape, dtype=bool)
    northern[:6, :] = True
    southern = np.zeros(GRID.shape, dtype=bool)
    southern[-6:, :] = True

    _, north_lat = preview_cube._water_lonlat(_cube_with_water(northern), GRID)  # type: ignore[misc]
    _, south_lat = preview_cube._water_lonlat(_cube_with_water(southern), GRID)  # type: ignore[misc]

    assert north_lat.mean() > south_lat.mean()


def test_no_water_and_no_landcover_both_return_nothing() -> None:
    """`None` rather than an empty array: the caller skips the overlay entirely."""
    assert preview_cube._water_lonlat(_cube_with_water(np.zeros(GRID.shape, bool)), GRID) is None

    empty = xr.Dataset(coords={"y": GRID.y_coords(), "x": GRID.x_coords()})
    assert preview_cube._water_lonlat(empty, GRID) is None


def test_resampling_names_resolve_to_rasterio_enums() -> None:
    """The cube declares resampling by name; a typo must fail here, not mid-render."""
    for name in ("nearest", "bilinear", "average"):
        assert preview_cube._rio_enum(name).name == name

    with pytest.raises(KeyError):
        preview_cube._rio_enum("not-a-resampling")
