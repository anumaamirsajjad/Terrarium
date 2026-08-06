"""A synthetic API runtime, built entirely in memory.

The route tests need a cube on the *real* canonical grid, because the whole point of
`/simulate` is that a WGS84 polygon rasterises onto that grid. What they must not need is
a Zarr store, a trained artefact, or a network - so this builds a small but structurally
honest cube and trains a genuine booster on it in a fraction of a second.

Training a real model rather than stubbing `predict` is deliberate: a stub cannot catch a
feature-column mismatch between the API's window slice and what the booster was fitted
on, which is exactly the kind of break that would reach production silently.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import xarray as xr
from fastapi.testclient import TestClient

from terrarium.api.runtime import Runtime
from terrarium.config import ACTIVE_TILE, Settings
from terrarium.cores.thermal.features import build_training_frame
from terrarium.cores.thermal.model import train
from terrarium.cores.thermal.simulate import BUILT_UP_CLASS, TREE_COVER_CLASS, WATER_CLASS
from terrarium.state.cube import summarise
from terrarium.state.grid import grid_for_tile

# Two windows so "the window is part of the answer" is testable, and one of each season
# so the summer default has something to prefer.
WINDOWS = ("2024-summer", "2024-winter")
SEASONS = ("summer", "winter")
# (air_temp_c, wind_speed_ms, relative_humidity_pct, wind_direction_deg)
MET_BY_WINDOW = {
    "2024-summer": (34.0, 2.25, 37.0, 250.0),
    "2024-winter": (14.1, 0.79, 71.0, 320.0),
}


def synthetic_cube() -> xr.Dataset:
    """A Lahore-shaped cube: real grid, plausible structure, invented numbers.

    Greenness varies **continuously** across the tile rather than taking one value on
    trees and another on buildings. That detail is load-bearing: a bimodal NDVI leaves a
    gap with no training examples in it, every split the booster learns sits inside that
    gap, and a +30 % canopy step lands short of all of them - so the model returns a
    delta of exactly zero and the intervention silently does nothing. Real NDVI is
    continuous, and a fixture that is not will pass tests the real cube would fail.
    """
    grid = grid_for_tile(ACTIVE_TILE)
    height, width = grid.shape

    # A smooth greenness field in [0, 1]: a diagonal gradient plus a couple of broad
    # lobes, so every intermediate value is populated somewhere on the tile.
    ys = np.linspace(0.0, 1.0, height)[:, None]
    xs = np.linspace(0.0, 1.0, width)[None, :]
    greenness = 0.5 * (ys + xs) / 2.0 + 0.35 * np.sin(3.0 * np.pi * ys) * np.cos(
        2.0 * np.pi * xs
    )
    greenness = np.clip((greenness - greenness.min()) / np.ptp(greenness), 0.0, 1.0)

    # Land cover follows the field rather than contradicting it, so "tree cover" really
    # is the green end of the distribution, as it is on the real tile.
    landcover = np.where(greenness > 0.78, TREE_COVER_CLASS, BUILT_UP_CLASS).astype("uint8")
    landcover[:, :6] = WATER_CLASS  # a river down the western edge: unplantable

    ndvi = 0.05 + 0.70 * greenness
    ndbi = 0.35 - 0.50 * greenness
    albedo = 0.22 - 0.09 * greenness
    elevation = 210.0 + np.linspace(0.0, 8.0, height)[:, None] * np.ones((1, width))
    population = np.where(landcover == WATER_CLASS, 0.0, 120.0).astype("float32")

    def stack(values: np.ndarray) -> np.ndarray:
        return np.repeat(np.asarray(values)[None, ...], len(WINDOWS), axis=0)

    # Warmer where there is less vegetation, offset by the window's air temperature.
    offsets = np.array([MET_BY_WINDOW[w][0] for w in WINDOWS], dtype="float64")
    lst = 12.0 * (1.0 - greenness)[None, ...] + offsets[:, None, None]

    met = {
        name: ("time", np.array([MET_BY_WINDOW[w][i] for w in WINDOWS], dtype="float32"))
        for i, name in enumerate(
            ("air_temp_c", "wind_speed_ms", "relative_humidity_pct", "wind_direction_deg")
        )
    }

    # A road grid: emissions on every tenth row and column, heavier where it is built up.
    # Sparse and structured, like the real inventory - a uniform field would let a plume
    # bug that smears everything look exactly like a correct answer.
    emissions = np.zeros((height, width), dtype="float32")
    emissions[::10, :] = 0.002
    emissions[:, ::10] = 0.002
    emissions[landcover == WATER_CLASS] = 0.0

    return xr.Dataset(
        {
            "pm25_emission_g_s": (("y", "x"), emissions),
            "lst_c": (("time", "y", "x"), lst.astype("float32")),
            "ndvi": (("time", "y", "x"), stack(ndvi).astype("float32")),
            "ndbi": (("time", "y", "x"), stack(ndbi).astype("float32")),
            "albedo": (("time", "y", "x"), stack(albedo).astype("float32")),
            "elevation_m": (("y", "x"), elevation.astype("float32")),
            "landcover": (("y", "x"), landcover),
            "population": (("y", "x"), population),
            **met,
        },
        coords={
            "y": grid.y_coords(),
            "x": grid.x_coords(),
            "time": np.array(
                ["2024-05-16", "2024-12-16"], dtype="datetime64[ns]"
            ),
            "window": ("time", np.array(WINDOWS, dtype="<U32")),
            "season": ("time", np.array(SEASONS, dtype="<U16")),
        },
    )


@pytest.fixture(scope="session")
def synthetic_runtime() -> Runtime:
    """A loaded runtime over the synthetic cube, with a genuinely trained booster."""
    cube = synthetic_cube()
    grid = grid_for_tile(ACTIVE_TILE)

    training = build_training_frame(cube)
    # Enough rounds for the booster to actually learn the greenness response, not just
    # the seasonal offset. Under ~100 it fits air temperature and little else, and every
    # intervention test then passes or fails on noise. Still ~2 s, and session-scoped.
    model = train(training.features, training.target, num_boost_round=200)

    return Runtime(
        cube=cube,
        model=model,
        grid=grid,
        summary=summarise(cube, grid),
        cube_path=Path("<synthetic>"),
        model_path=Path("<synthetic>"),
    )


@pytest.fixture(scope="session")
def client(synthetic_runtime: Runtime) -> TestClient:
    """A TestClient over an app wired to the synthetic runtime."""
    from terrarium.api.main import create_app

    settings = Settings(env="test")
    return TestClient(create_app(settings, runtime=synthetic_runtime))
