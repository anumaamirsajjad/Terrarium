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

import importlib
import os
from collections.abc import Callable, Iterator, Sequence
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
    greenness = 0.5 * (ys + xs) / 2.0 + 0.35 * np.sin(3.0 * np.pi * ys) * np.cos(2.0 * np.pi * xs)
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
            "time": np.array(["2024-05-16", "2024-12-16"], dtype="datetime64[ns]"),
            "window": ("time", np.array(WINDOWS, dtype="<U32")),
            "season": ("time", np.array(SEASONS, dtype="<U16")),
        },
    )


# Secrets a developer legitimately has in `.env`, and which no test may inherit.
AMBIENT_KEY_VARS = (
    "TERRARIUM_GEMINI_API_KEY",
    "TERRARIUM_GROQ_API_KEY",
    "TERRARIUM_OPENAQ_KEY",
)


@pytest.fixture(scope="session", autouse=True)
def no_ambient_keys() -> Iterator[None]:
    """Hide the developer's real keys from the whole suite.

    `Settings()` reads `.env`, so the moment somebody adds a working key the suite starts
    behaving differently on their machine than in CI — and not harmlessly. The tests that
    assert the *no-model* fallback — `/plan` parsing by rules rather than by a model — began
    failing the moment a key existed, and one of them stopped being an offline test at all:
    it reached Google, because a path that is supposed to stay offline without a model went
    and asked one.

    That is a direct breach of "no test may touch the network" (CLAUDE.md), and the CI
    network-isolation job would not have caught it, because CI has no key and so never
    takes that branch.

    Empty string rather than deleting the variable: an env var set to `""` still takes
    precedence over `.env`, whereas an unset one lets the file win. `resolve_adapter`
    treats it as absent, which is exactly the state these tests are written against.
    """
    saved = {name: os.environ.get(name) for name in AMBIENT_KEY_VARS}
    os.environ.update(dict.fromkeys(AMBIENT_KEY_VARS, ""))
    yield
    for name, value in saved.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


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


class ScriptedAdapter:
    """A stand-in for a configured provider. Answers each call from a script.

    The AI layer requires a key, so the routes that use it cannot be tested against a
    keyless app any more — but no test may touch the network either. This is the seam that
    resolves both: `resolve_adapter` is patched to return one of these, which satisfies
    `require_model` and answers deterministically.

    Repeats its last reply rather than raising when the script runs out: the graph decides
    how many times to ask, and a test that ran dry would fail with a `StopIteration`
    describing the fixture instead of the behaviour under test.

    A reply may be a **callable taking the prompt**, which lets a test's script react to
    what the prompt actually contains rather than hardcoding an answer that has to stay in
    sync with it by hand.
    """

    name = "scripted:test"

    def __init__(self, replies: Sequence[str | Callable[[str], str]]) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []

    def complete_json(self, *, system: str, user: str) -> str:
        self.prompts.append(user)
        reply = self.replies[min(len(self.prompts) - 1, len(self.replies) - 1)]
        return reply(user) if callable(reply) else reply


@pytest.fixture
def with_model(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[Sequence[str | Callable[[str], str]]], ScriptedAdapter]:
    """Configure a scripted model everywhere `resolve_adapter` is reached from.

    Patched per *importing module* rather than at the source, because each of them does
    `from terrarium.dsl.llm import resolve_adapter` and therefore holds its own reference.
    A single patch on `dsl.llm` would leave every one of them pointing at the real one.
    """

    def configure(replies: Sequence[str | Callable[[str], str]]) -> ScriptedAdapter:
        adapter = ScriptedAdapter(replies)
        for module in ("terrarium.api.deps", "terrarium.agent.nodes"):
            monkeypatch.setattr(
                importlib.import_module(module), "resolve_adapter", lambda *_a, **_k: adapter
            )
        return adapter

    return configure


@pytest.fixture(scope="session")
def client(synthetic_runtime: Runtime, no_ambient_keys: None) -> TestClient:
    """A TestClient over an app wired to the synthetic runtime, and to **no LLM**.

    `no_ambient_keys` is depended on explicitly rather than left to autouse ordering: this
    fixture is session-scoped and would otherwise be free to build before it, reading a
    real key straight out of `.env`. The tests that use this client are the ones asserting
    the offline fallbacks, so a key here does not weaken them — it inverts them.
    """
    settings = Settings(env="test", gemini_api_key=None, groq_api_key=None, openaq_key=None)
    from terrarium.api.main import create_app

    return TestClient(create_app(settings, runtime=synthetic_runtime))
