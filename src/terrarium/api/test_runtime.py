"""Startup: what the API loads once, and what it refuses to serve."""

from __future__ import annotations

from pathlib import Path

import lightgbm as lgb
import numpy as np
import pytest

from terrarium.api.conftest import synthetic_cube
from terrarium.api.runtime import Runtime, StartupError, load_runtime, spatial_variable
from terrarium.config import ACTIVE_TILE, Settings
from terrarium.cores.thermal.features import (
    FEATURE_NAMES,
    METEOROLOGY_VARIABLES,
    TrainingFrame,
    build_training_frame,
)
from terrarium.cores.thermal.model import train
from terrarium.state.cube import validate_windows, window_valid_fractions
from terrarium.state.grid import grid_for_tile
from terrarium.state.store import write_cube

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


# --------------------------------------------------- the stale-artefact guard ---
#
# These are the only tests that go through `load_runtime` with a real model *file*.
# Everything else builds a `Runtime` directly from a booster trained in-process against
# the current `FEATURE_NAMES`, so no other test can see an artefact on disk go stale.


def _booster_over(training: TrainingFrame, columns: list[str]) -> lgb.Booster:
    """A booster fitted on `columns` verbatim, bypassing `train`'s FEATURE_NAMES pin."""
    dataset = lgb.Dataset(training.features[columns], label=training.target, free_raw_data=False)
    return lgb.train({"objective": "regression", "verbose": -1}, dataset, num_boost_round=5)


def _write_cube_and_frame(tmp_path: Path) -> tuple[Path, TrainingFrame]:
    cube = synthetic_cube()
    grid = grid_for_tile(ACTIVE_TILE)
    cube_path = tmp_path / "cube.zarr"
    write_cube(cube, cube_path, grid=grid)
    return cube_path, build_training_frame(cube)


def test_a_model_trained_on_stale_features_is_refused_at_startup(tmp_path: Path) -> None:
    """The break that was actually shipped: a booster from before meteorology existed.

    It loads without complaint, serves /health and /cube/* correctly, and dies only on
    /simulate with a LightGBM shape error - a 500 on the one endpoint the product exists
    for, naming neither artefact. Startup is where this belongs.
    """
    cube_path, training = _write_cube_and_frame(tmp_path)

    # Built with lgb directly: `train` pins its columns to FEATURE_NAMES, which is the
    # right defence in-process and is also why the only way this breaks is an artefact
    # written by an older version of that constant.
    pre_meteorology = [n for n in FEATURE_NAMES if n not in METEOROLOGY_VARIABLES]
    stale = _booster_over(training, pre_meteorology)
    model_path = tmp_path / "thermal.txt"
    stale.save_model(str(model_path))

    settings = Settings(
        env="test", serve_zarr_store=cube_path, thermal_model_path=model_path
    )
    with pytest.raises(StartupError, match="different feature set"):
        load_runtime(settings)


def test_a_model_with_the_right_features_in_the_wrong_order_is_refused(
    tmp_path: Path,
) -> None:
    """LightGBM matches positionally, so this one predicts confidently and wrongly.

    No exception anywhere, no shape error, no NaN - just silently transposed features.
    A set comparison would pass it, which is why the guard compares the sequence.
    """
    cube_path, training = _write_cube_and_frame(tmp_path)

    reordered = [*FEATURE_NAMES[1:], FEATURE_NAMES[0]]
    model = _booster_over(training, reordered)
    model_path = tmp_path / "thermal.txt"
    model.save_model(str(model_path))

    settings = Settings(
        env="test", serve_zarr_store=cube_path, thermal_model_path=model_path
    )
    with pytest.raises(StartupError, match="different order"):
        load_runtime(settings)


def test_a_current_model_loads_and_can_simulate(tmp_path: Path) -> None:
    """The guard has to admit the artefact the training script actually writes."""
    cube_path, training = _write_cube_and_frame(tmp_path)

    model = train(training.features, training.target, num_boost_round=5)
    model_path = tmp_path / "thermal.txt"
    model.save_model(str(model_path))

    settings = Settings(
        env="test", serve_zarr_store=cube_path, thermal_model_path=model_path
    )
    runtime = load_runtime(settings)
    assert list(runtime.model.feature_name()) == list(FEATURE_NAMES)


def test_a_deployment_with_no_artefacts_answers_503_not_404(tmp_path: Path) -> None:
    """A missing cube must not look like a missing endpoint.

    The routes exist in this build either way, so 404 would tell the frontend `/simulate`
    is not a thing in this version - indistinguishable from a typo'd URL or an older
    deployment - when the truth is "not ready yet, here is why". /health stays 200 so a
    readiness probe still gets an answer instead of a crash loop.
    """
    from fastapi.testclient import TestClient

    from terrarium.api.main import create_app

    settings = Settings(
        env="test",
        serve_zarr_store=tmp_path / "absent.zarr",
        thermal_model_path=tmp_path / "absent.txt",
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").status_code == 200

        for path in ("/cube/summary", "/cube/layer/lst_c"):
            response = client.get(path)
            assert response.status_code == 503, path
            # The reason travels with it, rather than being buried in a server log.
            assert "no cube at" in response.json()["detail"]

        response = client.post(
            "/simulate",
            json={
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [[74.33, 31.51], [74.35, 31.51], [74.35, 31.53], [74.33, 31.51]]
                    ],
                },
                "canopy_fraction_added": 0.3,
            },
        )
        assert response.status_code == 503
