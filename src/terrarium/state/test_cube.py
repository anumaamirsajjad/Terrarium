"""Tests for the State Cube contract.

`validate_cube` is the guard that makes "if two layers don't align, that is a state/ bug"
checkable rather than aspirational. These are the cases it has to catch — and a guard
nobody has ever seen fail is a guard nobody knows works.

Nothing here touches the network: every cube is built in memory from the grid.
"""

from __future__ import annotations

import numpy as np
import pytest
import xarray as xr

from terrarium.config import SeasonWindow, Settings, Tile, season_windows
from terrarium.state.cube import (
    CUBE_VARIABLES,
    Dims,
    VariableSummary,
    empty_cube,
    select_window,
    summarise,
    validate_cube,
    window_labels,
)
from terrarium.state.grid import Grid, grid_for_tile

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
    return season_windows([2023, 2024])


def test_empty_cube_gives_every_variable_the_axes_it_declares(
    grid: Grid, windows: tuple[SeasonWindow, ...]
) -> None:
    cube = empty_cube(grid, windows)

    for spec in CUBE_VARIABLES:
        assert cube[spec.name].dims == spec.dims.axes, spec.name
    validate_cube(cube, grid, windows)


def test_static_variables_are_not_replicated_across_time(
    grid: Grid, windows: tuple[SeasonWindow, ...]
) -> None:
    """Storing four identical copies of the DEM would imply a variation never measured."""
    cube = empty_cube(grid, windows)

    assert "time" not in cube["elevation_m"].dims
    assert "time" not in cube["landcover"].dims
    assert "time" not in cube["population"].dims


def test_meteorology_is_a_series_not_a_map(
    grid: Grid, windows: tuple[SeasonWindow, ...]
) -> None:
    """One reanalysis point cannot resolve anything inside a 20 km tile."""
    cube = empty_cube(grid, windows)

    assert cube["air_temp_c"].dims == ("time",)
    assert cube["air_temp_c"].size == len(windows)


def test_window_and_season_coordinates_carry_the_meaning(
    grid: Grid, windows: tuple[SeasonWindow, ...]
) -> None:
    """`time` is a midpoint no satellite flew over; `window` is what you should cite."""
    cube = empty_cube(grid, windows)

    assert window_labels(cube) == [w.label for w in windows]
    assert [str(s) for s in cube["season"].values] == [str(w.season) for w in windows]
    # Monotonic, so any time-based selection behaves.
    times = cube["time"].values
    assert (np.diff(times) > np.timedelta64(0)).all()


def test_a_cube_needs_at_least_one_window(grid: Grid) -> None:
    with pytest.raises(ValueError, match="at least one"):
        empty_cube(grid, [])


def test_validate_rejects_a_variable_that_lost_its_time_axis(
    grid: Grid, windows: tuple[SeasonWindow, ...]
) -> None:
    """The likely regression: a per-window assignment that overwrote the whole variable."""
    cube = empty_cube(grid, windows)
    cube["lst_c"] = xr.DataArray(
        np.zeros(grid.shape, dtype="float32"), dims=("y", "x"), coords=grid.coords()
    )

    with pytest.raises(ValueError, match="dims are"):
        validate_cube(cube, grid, windows)


def test_validate_rejects_a_window_count_mismatch(
    grid: Grid, windows: tuple[SeasonWindow, ...]
) -> None:
    """Caught as a shape mismatch, which is what a wrong window count actually is."""
    cube = empty_cube(grid, windows)

    with pytest.raises(ValueError, match=r"shape \(4,"):
        validate_cube(cube, grid, windows[:-1])


def test_validate_rejects_the_right_count_of_wrong_windows(
    grid: Grid, windows: tuple[SeasonWindow, ...]
) -> None:
    """Same length, different seasons — shapes all agree, so only the labels catch it."""
    cube = empty_cube(grid, windows)
    other = season_windows([2019, 2020])

    with pytest.raises(ValueError, match="do not match"):
        validate_cube(cube, grid, other)


def test_validate_rejects_shifted_coordinates(
    grid: Grid, windows: tuple[SeasonWindow, ...]
) -> None:
    """Alignment is the cube's core promise; a half-pixel shift must not pass."""
    cube = empty_cube(grid, windows)
    cube = cube.assign_coords(x=cube["x"].values + grid.resolution_m / 2)

    with pytest.raises(ValueError, match="x coordinates"):
        validate_cube(cube, grid, windows)


def test_select_window_yields_the_two_dimensional_cube_a_core_consumes(
    grid: Grid, windows: tuple[SeasonWindow, ...]
) -> None:
    """Cores are written against one composite. This is what keeps that true."""
    cube = empty_cube(grid, windows)
    sliced = select_window(cube, windows[1].label)

    for spec in CUBE_VARIABLES:
        if spec.dims is Dims.TIME_SPACE:
            assert sliced[spec.name].dims == ("y", "x"), spec.name
        elif spec.dims is Dims.TIME:
            assert sliced[spec.name].shape == (), spec.name
    assert str(sliced["window"].values) == windows[1].label


def test_select_window_rejects_an_unknown_label(
    grid: Grid, windows: tuple[SeasonWindow, ...]
) -> None:
    with pytest.raises(ValueError, match="no window"):
        select_window(empty_cube(grid, windows), "1999-monsoon")


def test_selecting_a_window_reads_the_slice_it_names(
    grid: Grid, windows: tuple[SeasonWindow, ...]
) -> None:
    """Off-by-one here would silently train the thermal core on the wrong season."""
    cube = empty_cube(grid, windows)
    for index in range(len(windows)):
        cube["lst_c"].values[index] = float(index)

    for index, window in enumerate(windows):
        values = np.asarray(select_window(cube, window.label)["lst_c"].values)
        assert (values == index).all(), window.label


def test_summarise_reports_the_windows_it_covered(
    grid: Grid, windows: tuple[SeasonWindow, ...]
) -> None:
    summary = summarise(empty_cube(grid, windows), grid)

    assert summary.windows == [w.label for w in windows]
    # A wholly unpopulated cube is honest about it rather than reporting zeros.
    assert summary.missing == [spec.name for spec in CUBE_VARIABLES]


def test_settings_windows_match_the_declared_years() -> None:
    settings = Settings(window_years=[2022])
    assert [w.label for w in settings.windows] == ["2022-summer", "2022-winter"]


def test_a_handful_of_empty_pixels_never_reports_as_full_coverage() -> None:
    """The rounding that hid a real gap in a winter LST composite.

    9 unobserved pixels out of 40,602 is 99.978 % valid, and `.1%` renders that as a
    clean `100.0%` — indistinguishable from a complete map in the build report. Full
    coverage now prints without a decimal, so the two cannot be confused at a glance.
    """

    def coverage(fraction: float) -> str:
        return VariableSummary(
            name="lst_c",
            units="degC",
            dtype="float32",
            dims=Dims.TIME_SPACE,
            populated=True,
            valid_fraction=fraction,
        ).valid_text

    assert coverage(1.0) == "100%"
    assert coverage(1 - 9 / 40_602) == "99.978%"
    # Ordinary partial coverage stays readable rather than gaining noise decimals.
    assert coverage(0.739) == "73.9%"
