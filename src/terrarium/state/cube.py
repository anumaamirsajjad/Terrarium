"""The State Cube contract.

One xarray Dataset, one CRS, one resolution, one bbox, one set of coordinates. This
module declares *which* variables the cube holds and *how each must be resampled*, then
provides construction, validation, and summary helpers over that declaration.

The resampling policy lives here rather than in the pipeline because it is a property of
the variable's meaning, not of how it happens to be fetched: a land-cover class code is
nearest-neighbour no matter which collection it arrives from.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import cast

import numpy as np
import rioxarray  # noqa: F401  -- registers the .rio accessor used below
import xarray as xr
from pydantic import BaseModel, ConfigDict

from terrarium.config import (
    COLLECTION_DEM,
    COLLECTION_LANDSAT,
    COLLECTION_SENTINEL2,
    COLLECTION_WORLDCOVER,
    SOURCE_OPEN_METEO,
    SOURCE_OSM,
    SOURCE_WORLDPOP,
    SeasonWindow,
)
from terrarium.state.grid import Grid


class Resampling(StrEnum):
    """How a source's values are carried onto the analysis grid.

    NEAREST for anything whose values are *labels* — averaging class code 50 (built-up)
    with class code 10 (tree cover) yields class code 30, which is a lie. BILINEAR for
    anything whose values are *intensive* measurements: a temperature or an index has
    the same meaning whatever the pixel's area. SUM for *extensive* ones, where the value
    is a per-pixel total rather than a per-pixel rate — interpolating a head count
    invents or destroys people wherever the source and target pixel areas differ.
    """

    NEAREST = "nearest"
    BILINEAR = "bilinear"
    SUM = "sum"


class Dims(StrEnum):
    """Which axes a variable actually varies along.

    Not everything in the cube is a per-window map. Elevation and land cover do not
    change between windows, and meteorology from a single reanalysis point does not vary
    across the tile — broadcasting either into a full (time, y, x) array would store
    40,602 copies of one number and imply a variation that was never measured.
    """

    SPACE = "space"  # (y, x)      — static map
    TIME_SPACE = "time_space"  # (time, y, x) — one map per window
    TIME = "time"  # (time,)     — one scalar per window

    @property
    def axes(self) -> tuple[str, ...]:
        return {
            Dims.SPACE: ("y", "x"),
            Dims.TIME_SPACE: ("time", "y", "x"),
            Dims.TIME: ("time",),
        }[self]

    @property
    def is_spatial(self) -> bool:
        return self is not Dims.TIME


# Which script can put a variable into a cube that does not have one. These are the two
# remedies, and they are not interchangeable: `build_tile.py` is a network-heavy rebuild
# against Planetary Computer that takes ~70 s per window, while `build_air_layers.py`
# grafts two variables onto an existing cube in seconds. Telling someone to do the first
# when the second would do costs them minutes and a chance for the network to drop.
BUILD_TILE_SCRIPT = "scripts/build_tile.py"
BUILD_AIR_LAYERS_SCRIPT = "scripts/build_air_layers.py"


class VariableSpec(BaseModel):
    """Declaration of one cube variable."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    units: str
    dtype: str
    # The script that can add this variable to a cube built before it was declared.
    # Declared here rather than branched on in the validator, so that adding a variable
    # and adding the script that backfills it are one edit in one place.
    added_by: str = BUILD_TILE_SCRIPT
    # None for variables that never touch the grid — nothing is resampled onto a
    # dimension that does not exist.
    resampling: Resampling | None
    dims: Dims
    source_collection: str
    # Sentinel written where a pixel has no valid observation.
    fill_value: float
    # Physically possible range, inclusive. Values outside it are not extreme weather,
    # they are evidence of a scaling, offset, or masking bug, and are discarded rather
    # than passed to a core that would happily train on them.
    valid_range: tuple[float, float]

    @property
    def is_categorical(self) -> bool:
        return self.resampling is Resampling.NEAREST


CUBE_VARIABLES: tuple[VariableSpec, ...] = (
    VariableSpec(
        name="lst_c",
        # Landsat crosses Lahore at ~10:30 local and ST_B10 measures the *surface*, not
        # the air. Surface temperature runs several degrees above air temperature and
        # peaks well after the overpass, so this is never "temperature" unqualified and
        # never "afternoon". Keep the full phrase wherever this is shown (D9).
        description=(
            "Mid-morning land surface temperature (~10:30 local) from Landsat ST_B10, "
            "clear-sky median. Surface, not air temperature."
        ),
        units="degC",
        dtype="float32",
        resampling=Resampling.BILINEAR,
        dims=Dims.TIME_SPACE,
        source_collection=COLLECTION_LANDSAT,
        fill_value=float("nan"),
        valid_range=(-20.0, 80.0),
    ),
    VariableSpec(
        name="ndvi",
        description="Normalised difference vegetation index from Sentinel-2 B08/B04",
        units="1",
        dtype="float32",
        resampling=Resampling.BILINEAR,
        dims=Dims.TIME_SPACE,
        source_collection=COLLECTION_SENTINEL2,
        fill_value=float("nan"),
        valid_range=(-1.0, 1.0),
    ),
    VariableSpec(
        name="ndbi",
        description="Normalised difference built-up index from Sentinel-2 B11/B08",
        units="1",
        dtype="float32",
        resampling=Resampling.BILINEAR,
        dims=Dims.TIME_SPACE,
        source_collection=COLLECTION_SENTINEL2,
        fill_value=float("nan"),
        valid_range=(-1.0, 1.0),
    ),
    VariableSpec(
        name="albedo",
        description=(
            "Broadband shortwave albedo, Liang (2001) coefficients on Sentinel-2 bands"
        ),
        units="1",
        dtype="float32",
        resampling=Resampling.BILINEAR,
        dims=Dims.TIME_SPACE,
        source_collection=COLLECTION_SENTINEL2,
        fill_value=float("nan"),
        valid_range=(0.0, 1.0),
    ),
    VariableSpec(
        name="elevation_m",
        # GLO-30 is a Digital *Surface* Model: it includes buildings and canopy, so over
        # dense Lahore this reads several metres above bare ground. That is useful to the
        # thermal core as an urban-form proxy, but it is not terrain height - do not
        # treat it as such.
        description="Surface elevation (DSM, incl. buildings) above EGM2008, Copernicus DEM GLO-30",
        units="m",
        dtype="float32",
        resampling=Resampling.BILINEAR,
        dims=Dims.SPACE,
        source_collection=COLLECTION_DEM,
        fill_value=float("nan"),
        valid_range=(-500.0, 9000.0),
    ),
    VariableSpec(
        name="landcover",
        description="ESA WorldCover 2021 class code",
        units="class",
        dtype="uint8",
        resampling=Resampling.NEAREST,
        dims=Dims.SPACE,
        source_collection=COLLECTION_WORLDCOVER,
        fill_value=0.0,
        valid_range=(10.0, 100.0),
    ),
    VariableSpec(
        name="population",
        # A *count per cell*, not a density. That is why it resamples by SUM: the tile
        # total must survive reprojection from WGS84 onto the UTM grid. Phase 8's equity
        # panel divides cooling by this, so an inflated count silently deflates the
        # per-person benefit.
        description="Residents per 100 m cell, WorldPop 2020 constrained (BSGM)",
        units="people",
        dtype="float32",
        resampling=Resampling.SUM,
        dims=Dims.SPACE,
        source_collection=SOURCE_WORLDPOP,
        fill_value=float("nan"),
        # A 1 ha cell holding more than 10,000 people is 1 m2 per person: not a
        # neighbourhood, a units bug.
        valid_range=(0.0, 10_000.0),
    ),
    VariableSpec(
        name="pm25_emission_g_s",
        # Like population, this is a *per-cell total*, not a rate per unit area, so it
        # resamples by SUM. Unlike population it is never actually resampled: it is
        # assembled directly on the analysis grid by binning road samples, because OSM
        # arrives as vector geometry rather than as a raster with a resolution of its own.
        # The method is declared anyway - it is a property of the variable's meaning, and
        # the day something does resample it, averaging would be the wrong answer.
        description=(
            "Ground-level PM2.5 emission rate per 100 m cell: OSM road length x class "
            "traffic x fleet emission factor, plus brick-kiln point sources. A modelled "
            "inventory, not a measurement."
        ),
        units="g s-1",
        dtype="float32",
        added_by=BUILD_AIR_LAYERS_SCRIPT,
        resampling=Resampling.SUM,
        dims=Dims.SPACE,
        source_collection=SOURCE_OSM,
        # NaN, not 0.0, and the ingest writes explicit zeros where there are no roads.
        # Emissions are genuinely zero over most of the tile, so a 0.0 fill would make a
        # cube whose OSM ingest never ran indistinguishable from one whose tile is empty -
        # and it would report 100 % valid either way.
        fill_value=float("nan"),
        # A single 1 ha cell emitting 100 g/s is ~3 kt/year from one hectare: an inventory
        # bug, not a hotspot. The tile's busiest road cell is ~0.01 g/s.
        valid_range=(0.0, 100.0),
    ),
    # --- meteorology: one value per window, not a map ---------------------------
    #
    # Sampled at the ~10:30 local Landsat overpass hour and reduced by median over the
    # window's days, matching how the LST composite itself is built. A window mean over
    # all 24 hours would describe a different time of day than the temperature it is
    # meant to explain.
    VariableSpec(
        name="air_temp_c",
        # Not to be confused with lst_c. This is the *air* temperature 2 m above ground;
        # lst_c is the radiating surface, which runs several degrees hotter (D9).
        description=(
            "2 m air temperature at the ~10:30 local overpass hour, window median "
            "(Open-Meteo ERA5 archive). Air, not surface temperature."
        ),
        units="degC",
        dtype="float32",
        resampling=None,
        dims=Dims.TIME,
        source_collection=SOURCE_OPEN_METEO,
        fill_value=float("nan"),
        valid_range=(-30.0, 60.0),
    ),
    VariableSpec(
        name="wind_speed_ms",
        description=(
            "10 m wind speed at the ~10:30 local overpass hour, window median "
            "(Open-Meteo ERA5 archive)"
        ),
        units="m s-1",
        dtype="float32",
        resampling=None,
        dims=Dims.TIME,
        source_collection=SOURCE_OPEN_METEO,
        fill_value=float("nan"),
        valid_range=(0.0, 60.0),
    ),
    VariableSpec(
        name="wind_direction_deg",
        # Meteorological convention: the direction the wind blows *from*, clockwise from
        # north. 90 is an easterly, and the plume goes west. The air core depends on this
        # sign being right and nothing else in the cube would notice if it were not.
        #
        # Reduced by *vector* mean over the window, not median: 350 deg and 10 deg average
        # to 180 under any scalar reduction, which points the plume exactly backwards.
        description=(
            "10 m wind direction at the ~10:30 local overpass hour, window vector mean "
            "(Open-Meteo ERA5 archive). Meteorological convention: the direction the wind "
            "comes from, degrees clockwise from north."
        ),
        units="deg",
        dtype="float32",
        added_by=BUILD_AIR_LAYERS_SCRIPT,
        resampling=None,
        dims=Dims.TIME,
        source_collection=SOURCE_OPEN_METEO,
        fill_value=float("nan"),
        valid_range=(0.0, 360.0),
    ),
    VariableSpec(
        name="relative_humidity_pct",
        description=(
            "2 m relative humidity at the ~10:30 local overpass hour, window median "
            "(Open-Meteo ERA5 archive)"
        ),
        units="%",
        dtype="float32",
        resampling=None,
        dims=Dims.TIME,
        source_collection=SOURCE_OPEN_METEO,
        fill_value=float("nan"),
        valid_range=(0.0, 100.0),
    ),
)

VARIABLES_BY_NAME: dict[str, VariableSpec] = {spec.name: spec for spec in CUBE_VARIABLES}


class VariableSummary(BaseModel):
    """Value-range report for one variable. Used to sanity-check a built cube."""

    name: str
    units: str
    dtype: str
    dims: Dims
    populated: bool
    valid_fraction: float
    vmin: float | None = None
    vmax: float | None = None
    vmean: float | None = None

    @property
    def valid_text(self) -> str:
        """Coverage as text that cannot round a gap away.

        9 empty pixels out of 40,602 is 99.978 % valid, which `.1%` renders as a clean
        `100.0%` — which is exactly how a real gap in a winter LST composite went
        unnoticed in a build report. Full coverage prints without a decimal so the two
        can never be mistaken for each other, and anything short of it escalates
        precision rather than wearing a full-coverage label.
        """
        if self.valid_fraction >= 1.0:
            return "100%"
        text = f"{self.valid_fraction:.1%}"
        return f"{self.valid_fraction:.3%}" if text == "100.0%" else text


class CubeSummary(BaseModel):
    """Everything you need to decide whether a build is trustworthy."""

    crs: str
    resolution_m: int
    shape: tuple[int, int]
    windows: list[str]
    variables: list[VariableSummary]

    @property
    def populated(self) -> list[str]:
        return [v.name for v in self.variables if v.populated]

    @property
    def missing(self) -> list[str]:
        return [v.name for v in self.variables if not v.populated]


def time_coords(windows: Sequence[SeasonWindow]) -> dict[str, tuple[str, np.ndarray]]:
    """The cube's time axis: a datetime index plus the labels you should actually cite.

    `time` is the window midpoint, which no satellite ever flew over — a composite has no
    single acquisition date. `window` and `season` carry the meaning, and are what
    `select_window` and any narration should use.
    """
    return {
        "time": ("time", np.array([w.midpoint for w in windows], dtype="datetime64[ns]")),
        "window": ("time", np.array([w.label for w in windows], dtype="<U32")),
        "season": ("time", np.array([str(w.season) for w in windows], dtype="<U16")),
    }


def empty_cube(grid: Grid, windows: Sequence[SeasonWindow]) -> xr.Dataset:
    """A cube with every declared variable present but entirely unpopulated.

    The pipeline fills this in. Starting from a full skeleton means a source that fails
    to load leaves an all-fill variable rather than a missing key, so the cube's schema
    does not depend on the weather — or, now, on one window's scenes all being cloudy.
    """
    if not windows:
        raise ValueError("a cube needs at least one seasonal window")

    n_time = len(windows)
    coords: dict[str, object] = {**time_coords(windows), **grid.coords()}

    data: dict[str, xr.DataArray] = {}
    for spec in CUBE_VARIABLES:
        shape = {
            Dims.SPACE: grid.shape,
            Dims.TIME_SPACE: (n_time, *grid.shape),
            Dims.TIME: (n_time,),
        }[spec.dims]
        data[spec.name] = xr.DataArray(
            np.full(shape, spec.fill_value, dtype=spec.dtype),
            dims=spec.dims.axes,
        )

    ds = xr.Dataset(data, coords=coords)
    # rioxarray's accessor is untyped, hence the cast.
    ds = cast("xr.Dataset", ds.rio.write_crs(grid.crs))

    ds.attrs["crs"] = grid.crs
    ds.attrs["resolution_m"] = grid.resolution_m
    ds.attrs["bounds"] = list(grid.bounds)
    ds.attrs["windows"] = [w.label for w in windows]
    for spec in CUBE_VARIABLES:
        ds[spec.name].attrs.update(
            {
                "description": spec.description,
                "units": spec.units,
                "resampling": "none" if spec.resampling is None else str(spec.resampling),
                "source_collection": spec.source_collection,
            }
        )
    return ds


def validate_cube(ds: xr.Dataset, grid: Grid, windows: Sequence[SeasonWindow]) -> None:
    """Assert the cube honours its contract. Raises `ValueError` on the first breach.

    This is the guard that makes "if two layers don't align it's a state/ bug" checkable
    rather than aspirational.
    """
    missing = [spec.name for spec in CUBE_VARIABLES if spec.name not in ds.data_vars]
    if missing:
        raise ValueError(f"cube is missing declared variables: {missing}")

    n_time = len(windows)
    for spec in CUBE_VARIABLES:
        var = ds[spec.name]
        if var.dims != spec.dims.axes:
            raise ValueError(f"{spec.name}: dims are {var.dims}, expected {spec.dims.axes}")
        expected = {
            Dims.SPACE: grid.shape,
            Dims.TIME_SPACE: (n_time, *grid.shape),
            Dims.TIME: (n_time,),
        }[spec.dims]
        if var.shape != expected:
            raise ValueError(f"{spec.name}: shape {var.shape} != expected {expected}")

    if not np.allclose(ds["x"].values, grid.x_coords()):
        raise ValueError("cube x coordinates do not match the canonical grid")
    if not np.allclose(ds["y"].values, grid.y_coords()):
        raise ValueError("cube y coordinates do not match the canonical grid")

    labels = window_labels(ds)
    expected_labels = [w.label for w in windows]
    if labels != expected_labels:
        raise ValueError(f"cube windows {labels} do not match {expected_labels}")


def window_labels(ds: xr.Dataset) -> list[str]:
    """The cube's window labels, in time order."""
    return [str(label) for label in np.asarray(ds["window"].values).reshape(-1)]


def _valid_mask(values: np.ndarray, spec: VariableSpec) -> np.ndarray:
    """Which entries hold a real observation rather than the variable's fill value."""
    if spec.is_categorical:
        return np.asarray(values != np.asarray(spec.fill_value).astype(values.dtype))
    return np.asarray(np.isfinite(values))


def window_valid_fractions(ds: xr.Dataset) -> dict[str, dict[str, float]]:
    """Valid-value share of every per-window variable, per window.

    `summarise` reduces over the whole array, so a variable that loaded for two windows
    out of four still reads as 100 % populated. This is the per-window pass that catches
    the difference.
    """
    report: dict[str, dict[str, float]] = {}

    for label in window_labels(ds):
        window = select_window(ds, label)
        per_variable: dict[str, float] = {}
        for spec in CUBE_VARIABLES:
            if spec.dims is Dims.SPACE:
                continue  # static: identical in every window, nothing per-window to say
            if spec.name not in window:
                # A cube built before this variable was declared. Zero valid, not a
                # KeyError, so that a caller reporting coverage has a number for every
                # declared variable rather than a ragged table.
                #
                # `validate_windows` no longer reads this entry as "empty", though: it
                # asks `absent_variables` first and reports absence separately, because
                # absent and present-but-empty have different remedies (A8).
                per_variable[spec.name] = 0.0
                continue
            values = np.asarray(window[spec.name].values)
            valid = _valid_mask(values, spec)
            per_variable[spec.name] = float(valid.sum() / valid.size) if valid.size else 0.0
        report[label] = per_variable

    return report


def absent_variables(ds: xr.Dataset) -> list[str]:
    """Declared variables the cube does not carry *at all*, in declaration order.

    Deliberately separate from `window_valid_fractions`, and deliberately covering every
    `Dims` rather than only the per-window ones. A static variable is identical in every
    window, so the per-window pass skips it — correctly — and therefore never notices
    when it is missing entirely. `pm25_emission_g_s` is static, so a cube without an
    emission inventory used to start, serve maps, simulate temperatures, and return
    `air: null` for every traffic plan, with a log warning as the only trace. From the
    client's side that is indistinguishable from "this plan says nothing about traffic",
    and those are opposite findings.
    """
    return [spec.name for spec in CUBE_VARIABLES if spec.name not in ds.data_vars]


def static_valid_fractions(ds: xr.Dataset) -> dict[str, float]:
    """Valid-value share of every *static* variable it carries.

    The other half of the same hole: a static variable that is present but entirely fill
    value is as useless as an absent one, and no per-window pass will ever look at it.
    Absent variables are excluded here — `absent_variables` reports those, with a
    different remedy.
    """
    fractions: dict[str, float] = {}
    for spec in CUBE_VARIABLES:
        if spec.dims is not Dims.SPACE or spec.name not in ds.data_vars:
            continue
        values = np.asarray(ds[spec.name].values)
        valid = _valid_mask(values, spec)
        fractions[spec.name] = float(valid.sum() / valid.size) if valid.size else 0.0
    return fractions


def validate_windows(ds: xr.Dataset, *, minimum_valid_fraction: float = 0.0) -> None:
    """Reject a cube that has time slices nothing was ever written into.

    A half-built cube does not announce itself. An ingest that dies partway leaves the
    time axis at full length with the unreached windows still holding their fill value,
    and every downstream consumer accepts that quietly: `validate_cube` checks shapes and
    coordinates, `summarise` reduces over all windows at once, and `select_window` hands
    back a slice of NaN without complaint. That is exactly how a cube whose 2024 windows
    were entirely empty got as far as training a model on half the time axis it claimed.

    `minimum_valid_fraction` defaults to 0.0, which fails only on a *completely* empty
    variable-window. Raise it to demand real coverage - an API serving map tiles wants
    more than one valid pixel.

    Three failures, reported separately because they have three different remedies. A
    variable the cube never had needs a *graft* onto the existing cube, which takes
    seconds; a variable-window nothing was written into needs a *rebuild*, which is
    network-heavy minutes. Collapsing both into "rebuild it - see build_tile.py" sent the
    Phase 9 audit down the slow path for a cube that only needed two layers added.
    """
    absent = absent_variables(ds)
    absent_names = set(absent)

    empty_windows = [
        f"{name}@{label} ({fraction:.1%} valid)"
        for label, per_variable in window_valid_fractions(ds).items()
        for name, fraction in per_variable.items()
        if fraction <= minimum_valid_fraction and name not in absent_names
    ]
    empty_static: dict[str, list[str]] = {}
    for name, fraction in static_valid_fractions(ds).items():
        if fraction <= minimum_valid_fraction:
            script = VARIABLES_BY_NAME[name].added_by
            empty_static.setdefault(script, []).append(f"{name} ({fraction:.1%} valid)")

    problems: list[str] = []

    if absent:
        by_script: dict[str, list[str]] = {}
        for name in absent:
            by_script.setdefault(VARIABLES_BY_NAME[name].added_by, []).append(name)
        for script, names in sorted(by_script.items()):
            plural = "these variables" if len(names) > 1 else "this variable"
            them = "them" if len(names) > 1 else "it"
            problems.append(
                f"cube does not carry {', '.join(names)} at all, so it predates "
                f"{plural} rather than being a partial build. Add {them} with {script}."
            )

    if empty_windows:
        problems.append(
            "cube has unpopulated variable-windows, so it is a partial build: "
            f"{', '.join(empty_windows)}. Rebuild it - see {BUILD_TILE_SCRIPT}."
        )

    for script, entries in sorted(empty_static.items()):
        problems.append(
            "cube carries static variables that hold no valid data: "
            f"{', '.join(entries)}. Rewrite {'them' if len(entries) > 1 else 'it'} "
            f"with {script}."
        )

    if problems:
        raise ValueError(" ".join(problems))


def select_window(ds: xr.Dataset, label: str) -> xr.Dataset:
    """One window as a 2-D cube — the shape a physics core consumes.

    Cores are written against a single composite and stay that way: the time dimension is
    the *cube's* concern, and choosing which slice to simulate is the caller's. Keeping
    the selection here rather than inside a core is what stopped Phase 3 from reaching
    into `cores/`.
    """
    labels = window_labels(ds)
    if label not in labels:
        raise ValueError(f"no window {label!r} in cube; have {labels}")
    return ds.isel(time=labels.index(label))


def enforce_valid_range(data: xr.DataArray, spec: VariableSpec) -> tuple[xr.DataArray, int]:
    """Replace out-of-range values with the variable's fill value.

    Returns the cleaned array and how many pixels were discarded. A non-zero count is a
    signal worth surfacing: physically impossible values mean a scaling or masking bug
    upstream, not unusual conditions on the ground.
    """
    low, high = spec.valid_range

    if spec.is_categorical:
        # NaN has no uint8 representation; fall back to the declared sentinel.
        fill = np.asarray(spec.fill_value).astype(data.dtype)
        inside = (data >= low) & (data <= high)
        n_dropped = int((~inside).sum())
        return data.where(inside, fill).astype(data.dtype), n_dropped

    finite = np.isfinite(data)
    inside = (data >= low) & (data <= high)
    # Only count pixels that held an actual value and were rejected.
    n_dropped = int((finite & ~inside).sum())
    return data.where(inside).astype(data.dtype), n_dropped


def summarise(ds: xr.Dataset, grid: Grid) -> CubeSummary:
    """Compute per-variable value ranges over valid values only.

    Ranges span every window a variable holds. To judge one window on its own, summarise
    `select_window(ds, label)` — a variable that loaded for three windows out of four
    reads as fully populated here, which is exactly the case a per-window pass catches.
    """
    summaries: list[VariableSummary] = []

    for spec in CUBE_VARIABLES:
        # Absent, not merely empty: a cube written before the variable was declared. It
        # reports as unpopulated, which is what it is from a consumer's point of view.
        values = (
            np.asarray(ds[spec.name].values)
            if spec.name in ds
            else np.array([], dtype=spec.dtype)
        )
        if spec.is_categorical:
            valid = values != np.asarray(spec.fill_value).astype(values.dtype)
        else:
            valid = np.isfinite(values)

        n_valid = int(valid.sum())
        fraction = n_valid / values.size if values.size else 0.0

        if n_valid:
            good = values[valid].astype("float64")
            summaries.append(
                VariableSummary(
                    name=spec.name,
                    units=spec.units,
                    dtype=str(values.dtype),
                    dims=spec.dims,
                    populated=True,
                    valid_fraction=fraction,
                    vmin=float(good.min()),
                    vmax=float(good.max()),
                    vmean=float(good.mean()),
                )
            )
        else:
            summaries.append(
                VariableSummary(
                    name=spec.name,
                    units=spec.units,
                    dtype=str(values.dtype),
                    dims=spec.dims,
                    populated=False,
                    valid_fraction=0.0,
                )
            )

    return CubeSummary(
        crs=grid.crs,
        resolution_m=grid.resolution_m,
        shape=grid.shape,
        windows=window_labels(ds) if "window" in ds.coords else [],
        variables=summaries,
    )
