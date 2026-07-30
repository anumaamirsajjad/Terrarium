"""Tests for Zarr and DuckDB persistence.

A cube build spends ten minutes on the network and then writes. If the write is what
breaks, you find out at the end of every one of those ten minutes — so the round trip is
worth checking in a second, offline, against a synthetic cube.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import xarray as xr

from terrarium.config import SeasonWindow, Tile, season_windows
from terrarium.state.cube import CUBE_VARIABLES, empty_cube, summarise, validate_cube, window_labels
from terrarium.state.grid import Grid, grid_for_tile
from terrarium.state.store import (
    BuildRecord,
    SourceRecord,
    connect_catalog,
    open_cube,
    record_build,
    write_cube,
)

TEST_TILE = Tile(
    name="TestTile",
    country="PK",
    bbox=(74.30, 31.50, 74.32, 31.52),
    crs="EPSG:32643",
    target_resolution_m=100,
)


@pytest.fixture
def grid() -> Grid:
    return grid_for_tile(TEST_TILE)


@pytest.fixture
def windows() -> tuple[SeasonWindow, ...]:
    return season_windows([2024])


@pytest.fixture
def cube(grid: Grid, windows: tuple[SeasonWindow, ...]) -> xr.Dataset:
    """A cube with a distinct value per window, so a slice mix-up is visible."""
    ds = empty_cube(grid, windows)
    for index in range(len(windows)):
        ds["lst_c"].values[index] = 30.0 + index
        ds["air_temp_c"].values[index] = 20.0 + index
    ds["elevation_m"].values[...] = 217.0
    ds["landcover"].values[...] = 50
    ds["population"].values[...] = 4.0
    return ds


def test_cube_survives_a_zarr_round_trip(
    tmp_path: Path, cube: xr.Dataset, grid: Grid, windows: tuple[SeasonWindow, ...]
) -> None:
    path = write_cube(cube, tmp_path / "cube.zarr", grid=grid)
    reopened = open_cube(path)

    # Still a valid cube by its own contract, not merely readable.
    validate_cube(reopened, grid, windows)
    for spec in CUBE_VARIABLES:
        assert reopened[spec.name].dims == spec.dims.axes, spec.name


def test_window_labels_survive_the_round_trip(
    tmp_path: Path, cube: xr.Dataset, grid: Grid, windows: tuple[SeasonWindow, ...]
) -> None:
    """The labels are string coordinates — the part of the schema Zarr is fussiest about."""
    reopened = open_cube(write_cube(cube, tmp_path / "cube.zarr", grid=grid))

    assert window_labels(reopened) == [w.label for w in windows]
    assert [str(s) for s in reopened["season"].values] == [str(w.season) for w in windows]


def test_per_window_values_are_not_scrambled(
    tmp_path: Path, cube: xr.Dataset, grid: Grid, windows: tuple[SeasonWindow, ...]
) -> None:
    reopened = open_cube(write_cube(cube, tmp_path / "cube.zarr", grid=grid))

    for index in range(len(windows)):
        assert np.allclose(reopened["lst_c"].values[index], 30.0 + index)
        assert np.allclose(reopened["air_temp_c"].values[index], 20.0 + index)
    assert np.allclose(reopened["elevation_m"].values, 217.0)


def test_scalar_series_are_written_without_spatial_chunking(
    tmp_path: Path, cube: xr.Dataset, grid: Grid
) -> None:
    """A (time,) variable has no y/x to chunk; asking for one is a write-time error."""
    reopened = open_cube(write_cube(cube, tmp_path / "cube.zarr", grid=grid))
    assert reopened["air_temp_c"].dims == ("time",)


def test_catalogue_records_the_windows_a_build_covered(
    tmp_path: Path, cube: xr.Dataset, grid: Grid, windows: tuple[SeasonWindow, ...]
) -> None:
    conn = connect_catalog(tmp_path / "catalog.duckdb")
    record_build(
        conn,
        BuildRecord(
            build_id="test123",
            tile_name=TEST_TILE.name,
            zarr_path=tmp_path / "cube.zarr",
            summary=summarise(cube, grid),
            sources=[
                SourceRecord(
                    collection_id="sentinel-2-l2a",
                    n_found=30,
                    n_kept=6,
                    n_composited=6,
                    window=windows[0].label,
                ),
                # Static sources belong to no window.
                SourceRecord(collection_id="worldpop", n_found=1, n_kept=1, n_composited=1),
            ],
            duration_s=1.0,
        ),
    )

    stored = conn.execute("SELECT windows FROM builds WHERE build_id = 'test123'").fetchone()
    assert stored is not None
    assert stored[0] == ",".join(w.label for w in windows)

    rows = conn.execute(
        "SELECT collection_id, window_label FROM build_sources ORDER BY collection_id"
    ).fetchall()
    assert rows == [("sentinel-2-l2a", windows[0].label), ("worldpop", None)]
    conn.close()


def test_catalogue_schema_migrates_an_existing_database(tmp_path: Path) -> None:
    """A Phase 1/2 catalogue must gain the new columns, not need deleting.

    The ALTER TABLE guard in the schema is what makes this true; without it the second
    connect is a no-op and every insert fails on an unknown column.
    """
    path = tmp_path / "catalog.duckdb"
    conn = connect_catalog(path)
    conn.execute(
        "INSERT INTO builds (build_id, built_at, tile_name, crs, resolution_m, height, "
        "width, zarr_path) VALUES ('old', now(), 'Lahore', 'EPSG:32643', 100, 201, 202, 'x')"
    )
    conn.close()

    # Reconnecting applies the schema again, exactly as a later build would.
    conn = connect_catalog(path)
    columns = {row[0] for row in conn.execute("DESCRIBE builds").fetchall()}
    assert "windows" in columns
    remaining = conn.execute("SELECT count(*) FROM builds").fetchone()
    assert remaining is not None and remaining[0] == 1
    conn.close()
