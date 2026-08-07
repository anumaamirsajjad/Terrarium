"""`build_tile.py`'s gap detection — the check that makes a half-built cube fail loudly.

The build's exit code is the only thing standing between a partial ingest and an artefact
that opens cleanly, summarises cleanly, and is missing a whole season. `_print_per_window`
is what finds those gaps, and it exists precisely because the whole-cube summary reports a
variable as populated when *any* window carried data.

Every finding in this repo's audit that began "a cube that opens is not a cube that is
complete" traces back to this function returning an empty list when it should not have.
"""

from __future__ import annotations

from typing import Any

import build_tile
import numpy as np
import xarray as xr

from terrarium.config import ACTIVE_TILE
from terrarium.state.cube import CUBE_VARIABLES, Dims, summarise
from terrarium.state.grid import grid_for_tile

GRID = grid_for_tile(ACTIVE_TILE)
WINDOWS = ("2024-summer", "2024-winter")


def _cube(*, ndvi_valid: tuple[float, float] = (1.0, 1.0)) -> xr.Dataset:
    """A complete two-window cube, with each window's NDVI populated to a given share.

    Built by iterating the **declared** variables rather than listing them here, so adding
    a variable to `state.cube` does not silently turn this fixture into a partial build and
    make every test below fail for a reason that has nothing to do with what it asserts.
    """
    height, width = GRID.shape
    cells = height * width
    arrays: dict[str, Any] = {}

    for spec in CUBE_VARIABLES:
        if spec.dims is Dims.SPACE:
            arrays[spec.name] = (("y", "x"), np.ones((height, width), dtype="float32"))
        elif spec.dims is Dims.TIME:
            arrays[spec.name] = ("time", np.array([34.0, 14.0], dtype="float32"))
        else:
            arrays[spec.name] = (
                ("time", "y", "x"),
                np.ones((len(WINDOWS), height, width), dtype="float32"),
            )

    ndvi = np.full((len(WINDOWS), height, width), np.nan, dtype="float32")
    for i, share in enumerate(ndvi_valid):
        keep = round(share * cells)
        ndvi[i].reshape(-1)[:keep] = 0.3
    arrays["ndvi"] = (("time", "y", "x"), ndvi)

    return xr.Dataset(
        arrays,
        coords={
            "y": GRID.y_coords(),
            "x": GRID.x_coords(),
            "time": np.array(["2024-05-16", "2024-12-16"], dtype="datetime64[ns]"),
            "window": ("time", np.array(WINDOWS, dtype="<U32")),
            "season": ("time", np.array(["summer", "winter"], dtype="<U16")),
        },
    )


def test_a_complete_cube_reports_no_gaps() -> None:
    cube = _cube()

    gaps = build_tile._print_per_window(cube, GRID, summarise(cube, GRID))

    assert gaps == []


def test_one_empty_window_is_reported_as_a_gap() -> None:
    """The failure that produced audit finding A1: a build that died partway.

    Window one is fully populated, so the pooled summary calls NDVI populated and every
    shape, coordinate and whole-cube check passes. Only the per-window pass sees it.
    """
    cube = _cube(ndvi_valid=(1.0, 0.0))

    gaps = build_tile._print_per_window(cube, GRID, summarise(cube, GRID))

    assert gaps == ["2024-winter/ndvi"]


def test_every_empty_window_is_named_not_just_the_first() -> None:
    """A build that died early leaves several, and the operator needs all of them."""
    cube = _cube(ndvi_valid=(0.0, 0.0))

    gaps = build_tile._print_per_window(cube, GRID, summarise(cube, GRID))

    assert gaps == ["2024-summer/ndvi", "2024-winter/ndvi"]


def test_static_variables_are_not_reported_per_window() -> None:
    """A static layer is identical in every window; listing it twice is noise.

    It is checked, but by `state.cube.absent_variables` rather than here — this pass is
    only about variables that vary along time.
    """
    cube = _cube()

    build_tile._print_per_window(cube, GRID, summarise(cube, GRID))

    temporal = [
        v.name
        for v in summarise(cube, GRID).variables
        if v.dims is not Dims.SPACE
    ]
    assert "elevation_m" not in temporal
    assert {"ndvi", "lst_c", "air_temp_c"} <= set(temporal)


def test_a_partially_populated_window_is_not_a_gap() -> None:
    """Cloud leaves a real window short of 100 %, which is a quality note, not a gap.

    The distinction matters: a gap fails the build, and failing on ordinary cloud cover
    would make the exit code useless.
    """
    cube = _cube(ndvi_valid=(1.0, 0.4))

    gaps = build_tile._print_per_window(cube, GRID, summarise(cube, GRID))

    assert gaps == []
