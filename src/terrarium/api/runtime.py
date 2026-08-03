"""Everything the API loads once, at startup.

This is the composition root's state. Layer 3 is the only layer allowed to open files
around a core: `cores/` takes the cube and the booster as arguments precisely so that
loading them can happen here, once, instead of per request.

Loading per request would cost a Zarr open and a model parse on every call and put the
< 3 s warm target out of reach immediately. It would also make the cube's validity a
per-request question, when it is really a per-deployment one.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import lightgbm as lgb
import xarray as xr

from terrarium.config import Season, Settings
from terrarium.state.cube import (
    VARIABLES_BY_NAME,
    CubeSummary,
    summarise,
    validate_windows,
    window_labels,
)
from terrarium.state.grid import Grid, grid_for_tile
from terrarium.state.store import open_cube

# The API refuses a cube whose windows are not genuinely populated. 0.5 rather than 0.0:
# a window that is 3 % valid will not render as a map and will not simulate sensibly, so
# accepting it only defers the failure to somewhere less legible than startup.
MINIMUM_WINDOW_VALID_FRACTION = 0.5


class StartupError(RuntimeError):
    """Startup failed. Raised with a message that says how to fix it."""


@dataclass(frozen=True)
class Runtime:
    """The loaded, validated artefacts every route shares.

    Frozen and built once. Routes read from it and never mutate it, which is what keeps
    them thin and keeps request handling free of I/O.
    """

    cube: xr.Dataset
    model: lgb.Booster
    grid: Grid
    summary: CubeSummary
    cube_path: Path
    model_path: Path

    @property
    def windows(self) -> list[str]:
        return window_labels(self.cube)

    def resolve_window(self, requested: str | None) -> str:
        """Validate a requested window, or pick the default.

        **The window is never silently defaulted to whatever is first.** Winter's
        modelled cooling is a quarter of summer's for the same intervention (Phase 4), so
        answering a summer question with a winter window would understate it by 4x with
        nothing on screen to say why. The default is the *latest summer*, and every
        response echoes the window it actually used.
        """
        available = self.windows
        if requested is not None:
            if requested not in available:
                raise KeyError(requested)
            return requested
        return self.default_window()

    def default_window(self) -> str:
        """The latest summer window, or the latest window if the cube has no summer."""
        available = self.windows
        summers = [label for label in available if label.endswith(str(Season.SUMMER))]
        return (summers or available)[-1]


def load_runtime(settings: Settings) -> Runtime:
    """Open the cube and the model, and refuse to serve either if it is not sound.

    Failing loudly here is the whole point. A partially built cube is invisible to every
    other check - `validate_cube` looks at shapes, `summarise` reduces over all windows
    at once - so without `validate_windows` the API would happily serve a NaN raster to
    the map and a zero delta to the simulator, both of which look like answers.
    """
    cube_path = settings.serve_zarr_store
    model_path = settings.thermal_model_path

    if not cube_path.exists():
        raise StartupError(
            f"no cube at {cube_path}. Build one with scripts/build_tile.py, or point "
            "TERRARIUM_SERVE_ZARR_STORE at an existing build."
        )
    if not model_path.exists():
        raise StartupError(
            f"no thermal model at {model_path}. Train one with scripts/train_thermal.py."
        )

    cube = open_cube(cube_path)
    try:
        validate_windows(cube, minimum_valid_fraction=MINIMUM_WINDOW_VALID_FRACTION)
    except ValueError as exc:
        raise StartupError(f"cube at {cube_path} is not servable: {exc}") from exc

    grid = grid_for_tile(settings.tile)
    model = lgb.Booster(model_file=str(model_path))

    return Runtime(
        cube=cube,
        model=model,
        grid=grid,
        summary=summarise(cube, grid),
        cube_path=cube_path,
        model_path=model_path,
    )


def spatial_variable(name: str) -> str:
    """Validate that `name` is a cube variable that can be drawn as a map.

    Meteorology is `(time,)` - one reanalysis value per window, no spatial structure.
    Rendering it as a raster would paint one number across 40,602 pixels and imply a
    field nobody measured, which is the exact mistake the cube's `Dims` split prevents.
    """
    spec = VARIABLES_BY_NAME.get(name)
    if spec is None:
        raise KeyError(name)
    if not spec.dims.is_spatial:
        raise ValueError(
            f"{name} varies only along time - it is one value per window, not a map. "
            "Read it from /cube/summary instead."
        )
    return name
