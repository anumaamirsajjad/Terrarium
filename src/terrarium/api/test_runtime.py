"""Startup: what the API loads once, and what it refuses to serve."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from terrarium.api.conftest import synthetic_cube
from terrarium.api.runtime import Runtime, StartupError, load_runtime, spatial_variable
from terrarium.config import Settings
from terrarium.state.cube import validate_windows, window_valid_fractions

# ---------------------------------------------------------------- window choice ---


def test_the_default_window_is_the_latest_summer(synthetic_runtime: Runtime) -> None:
    """Not the first window, and not the last one.

    Winter cools a quarter as much for the same planting, so defaulting to whichever
    slice happens to be last would understate a summer question by 4x with nothing on
    screen to explain it.
    """
    assert synthetic_runtime.default_window() == "2024-summer"
    assert synthetic_runtime.windows[-1] == "2024-winter"


def test_an_explicit_window_is_honoured(synthetic_runtime: Runtime) -> None:
    assert synthetic_runtime.resolve_window("2024-winter") == "2024-winter"


def test_an_unknown_window_raises_rather_than_falling_back(synthetic_runtime: Runtime) -> None:
    """Silently substituting the default would answer a different question than asked."""
    with pytest.raises(KeyError):
        synthetic_runtime.resolve_window("1999-summer")


# ------------------------------------------------------------ variable validity ---


def test_meteorology_is_not_a_drawable_layer() -> None:
    with pytest.raises(ValueError, match="not a map"):
        spatial_variable("air_temp_c")


def test_an_unknown_variable_raises() -> None:
    with pytest.raises(KeyError):
        spatial_variable("nonsense")


@pytest.mark.parametrize("name", ["lst_c", "ndvi", "elevation_m", "landcover", "population"])
def test_spatial_variables_are_drawable(name: str) -> None:
    assert spatial_variable(name) == name


# ------------------------------------------------------- the half-built-cube guard ---


def test_a_full_cube_passes_per_window_validation() -> None:
    validate_windows(synthetic_cube(), minimum_valid_fraction=0.5)


def test_an_empty_window_is_caught() -> None:
    """The failure that got a model trained on half the time axis it claimed.

    `validate_cube` checks shapes and coordinates, `summarise` reduces over every window
    at once, and `select_window` returns a slice of NaN without complaint - so nothing
    else in the stack notices. This is the check that does.
    """
    cube = synthetic_cube()
    # Blank the second window's optical variables, exactly as a died-partway ingest does.
    for name in ("ndvi", "ndbi", "albedo"):
        cube[name].values[1] = np.nan

    with pytest.raises(ValueError, match="partial build"):
        validate_windows(cube)

    fractions = window_valid_fractions(cube)
    assert fractions["2024-winter"]["ndvi"] == 0.0
    # The window that survived is still reported as fine, so the message points at the
    # actual gap rather than condemning the whole cube.
    assert fractions["2024-summer"]["ndvi"] == 1.0


def test_a_thin_window_fails_the_stricter_threshold() -> None:
    """Not empty, but too sparse to render or simulate sensibly."""
    cube = synthetic_cube()
    values = cube["lst_c"].values
    values[1, 20:, :] = np.nan  # leave ~10 % valid

    validate_windows(cube)  # passes the "completely empty" bar
    with pytest.raises(ValueError, match="partial build"):
        validate_windows(cube, minimum_valid_fraction=0.5)


def test_static_variables_are_not_reported_per_window() -> None:
    """They are identical in every slice; repeating them would be noise, not information."""
    fractions = window_valid_fractions(synthetic_cube())

    assert "elevation_m" not in fractions["2024-summer"]
    assert "population" not in fractions["2024-summer"]
    assert "lst_c" in fractions["2024-summer"]


# ------------------------------------------------------------------- load_runtime ---


def test_a_missing_cube_is_a_clear_startup_error(tmp_path: Path) -> None:
    settings = Settings(
        env="test",
        serve_zarr_store=tmp_path / "absent.zarr",
        thermal_model_path=tmp_path / "absent.txt",
    )

    with pytest.raises(StartupError, match="no cube at"):
        load_runtime(settings)


def test_a_missing_model_is_a_clear_startup_error(tmp_path: Path) -> None:
    cube_path = tmp_path / "cube.zarr"
    cube_path.mkdir()
    settings = Settings(
        env="test",
        serve_zarr_store=cube_path,
        thermal_model_path=tmp_path / "absent.txt",
    )

    with pytest.raises(StartupError, match="no thermal model at"):
        load_runtime(settings)
