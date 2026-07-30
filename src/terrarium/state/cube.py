"""The State Cube contract.

One xarray Dataset, one CRS, one resolution, one bbox, one set of coordinates. This
module declares *which* variables the cube holds and *how each must be resampled*, then
provides construction, validation, and summary helpers over that declaration.

The resampling policy lives here rather than in the pipeline because it is a property of
the variable's meaning, not of how it happens to be fetched: a land-cover class code is
nearest-neighbour no matter which collection it arrives from.
"""

from __future__ import annotations

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
)
from terrarium.state.grid import Grid


class Resampling(StrEnum):
    """The only two resampling methods v1 uses.

    NEAREST for anything whose values are *labels* — averaging class code 50 (built-up)
    with class code 10 (tree cover) yields class code 30, which is a lie. BILINEAR for
    anything whose values are *measurements* on a continuous scale.
    """

    NEAREST = "nearest"
    BILINEAR = "bilinear"


class VariableSpec(BaseModel):
    """Declaration of one cube variable."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    units: str
    dtype: str
    resampling: Resampling
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
        description="Land surface temperature from Landsat ST_B10, clear-sky median",
        units="degC",
        dtype="float32",
        resampling=Resampling.BILINEAR,
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
        source_collection=COLLECTION_SENTINEL2,
        fill_value=float("nan"),
        valid_range=(-1.0, 1.0),
    ),
    VariableSpec(
        name="albedo",
        description="Broadband shortwave albedo, Liang (2001) Sentinel-2 approximation",
        units="1",
        dtype="float32",
        resampling=Resampling.BILINEAR,
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
        source_collection=COLLECTION_WORLDCOVER,
        fill_value=0.0,
        valid_range=(10.0, 100.0),
    ),
)

VARIABLES_BY_NAME: dict[str, VariableSpec] = {spec.name: spec for spec in CUBE_VARIABLES}


class VariableSummary(BaseModel):
    """Value-range report for one variable. Used to sanity-check a built cube."""

    name: str
    units: str
    dtype: str
    populated: bool
    valid_fraction: float
    vmin: float | None = None
    vmax: float | None = None
    vmean: float | None = None


class CubeSummary(BaseModel):
    """Everything you need to decide whether a build is trustworthy."""

    crs: str
    resolution_m: int
    shape: tuple[int, int]
    variables: list[VariableSummary]

    @property
    def populated(self) -> list[str]:
        return [v.name for v in self.variables if v.populated]

    @property
    def missing(self) -> list[str]:
        return [v.name for v in self.variables if not v.populated]


def empty_cube(grid: Grid) -> xr.Dataset:
    """A cube with every declared variable present but entirely unpopulated.

    The pipeline fills this in. Starting from a full skeleton means a source that fails
    to load leaves an all-fill variable rather than a missing key, so the cube's schema
    does not depend on the weather.
    """
    data = {
        spec.name: grid.empty(fill=spec.fill_value, dtype=spec.dtype) for spec in CUBE_VARIABLES
    }
    ds = xr.Dataset(data, coords=grid.coords())
    # rioxarray's accessor is untyped, hence the cast.
    ds = cast("xr.Dataset", ds.rio.write_crs(grid.crs))

    ds.attrs["crs"] = grid.crs
    ds.attrs["resolution_m"] = grid.resolution_m
    ds.attrs["bounds"] = list(grid.bounds)
    for spec in CUBE_VARIABLES:
        ds[spec.name].attrs.update(
            {
                "description": spec.description,
                "units": spec.units,
                "resampling": str(spec.resampling),
                "source_collection": spec.source_collection,
            }
        )
    return ds


def validate_cube(ds: xr.Dataset, grid: Grid) -> None:
    """Assert the cube honours its contract. Raises `ValueError` on the first breach.

    This is the guard that makes "if two layers don't align it's a state/ bug" checkable
    rather than aspirational.
    """
    missing = [spec.name for spec in CUBE_VARIABLES if spec.name not in ds.data_vars]
    if missing:
        raise ValueError(f"cube is missing declared variables: {missing}")

    expected_x = grid.x_coords()
    expected_y = grid.y_coords()

    for name in (spec.name for spec in CUBE_VARIABLES):
        var = ds[name]
        if var.dims != ("y", "x"):
            raise ValueError(f"{name}: dims are {var.dims}, expected ('y', 'x')")
        if var.shape != grid.shape:
            raise ValueError(f"{name}: shape {var.shape} != grid shape {grid.shape}")

    if not np.allclose(ds["x"].values, expected_x):
        raise ValueError("cube x coordinates do not match the canonical grid")
    if not np.allclose(ds["y"].values, expected_y):
        raise ValueError("cube y coordinates do not match the canonical grid")


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
    """Compute per-variable value ranges over valid pixels only."""
    summaries: list[VariableSummary] = []

    for spec in CUBE_VARIABLES:
        values = np.asarray(ds[spec.name].values)
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
                    populated=False,
                    valid_fraction=0.0,
                )
            )

    return CubeSummary(
        crs=grid.crs,
        resolution_m=grid.resolution_m,
        shape=grid.shape,
        variables=summaries,
    )
