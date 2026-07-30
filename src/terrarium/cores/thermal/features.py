"""Cube -> feature matrix for the thermal emulator.

One row per pixel, in row-major (y, x) order, so a prediction vector reshapes straight
back onto the grid. Pure: arrays in, DataFrame out.

The neighbourhood means are the highest-value feature here. Land surface temperature at a
point is driven by the surrounding few hundred metres, not by the 100 m cell alone - and
they are also what makes an intervention's cooling extend past the polygon the user drew.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import xarray as xr
from scipy.ndimage import uniform_filter

# The cube variables the model reads. `lst_c` is the target, not a feature.
BASE_VARIABLES: tuple[str, ...] = ("ndvi", "ndbi", "albedo", "elevation_m", "landcover")

TARGET_VARIABLE = "lst_c"

# 5 cells at 100 m = a 500 m window.
NEIGHBOURHOOD_CELLS = 5

FEATURE_NAMES: tuple[str, ...] = (
    "ndvi",
    "ndbi",
    "albedo",
    "elevation_m",
    "landcover",
    "ndvi_mean_500m",
    "ndbi_mean_500m",
)

# Land cover is a class code. Averaging it, or letting a tree split on 10 < 30 < 50,
# is arithmetic on labels - LightGBM must treat it as categorical.
CATEGORICAL_FEATURES: tuple[str, ...] = ("landcover",)


def neighbourhood_mean(values: np.ndarray, size: int = NEIGHBOURHOOD_CELLS) -> np.ndarray:
    """NaN-aware uniform mean over a `size` x `size` window.

    `uniform_filter` alone would smear a single NaN across its whole window, which over a
    tile with any masked pixels quietly deletes the feature. Averaging only the valid
    members instead keeps the edges and the river banks usable.
    """
    valid = np.isfinite(values)
    total = uniform_filter(np.where(valid, values, 0.0), size=size, mode="nearest")
    share = uniform_filter(valid.astype("float64"), size=size, mode="nearest")
    return np.where(share > 0, total / np.where(share > 0, share, 1.0), np.nan)


def features_from_arrays(arrays: dict[str, np.ndarray]) -> tuple[pd.DataFrame, np.ndarray]:
    """Build the feature matrix from raw (y, x) arrays.

    Separate from `build_features` so `simulate` can perturb the arrays and rebuild -
    including the neighbourhood terms, which must be recomputed from the *perturbed*
    field rather than carried over from the baseline.

    Returns the frame (one row per pixel) and a (y, x) boolean mask of rows whose
    features are all usable.
    """
    missing = [name for name in BASE_VARIABLES if name not in arrays]
    if missing:
        raise ValueError(f"features need cube variables that are absent: {missing}")

    ndvi = arrays["ndvi"].astype("float64")
    ndbi = arrays["ndbi"].astype("float64")
    landcover = arrays["landcover"]

    columns = {
        "ndvi": ndvi,
        "ndbi": ndbi,
        "albedo": arrays["albedo"].astype("float64"),
        "elevation_m": arrays["elevation_m"].astype("float64"),
        # 0 is WorldCover's no-data sentinel; as a category it is a real, if useless,
        # level. It is excluded from training by the valid mask below.
        "landcover": landcover.astype("int16"),
        "ndvi_mean_500m": neighbourhood_mean(ndvi),
        "ndbi_mean_500m": neighbourhood_mean(ndbi),
    }

    shape = ndvi.shape
    frame = pd.DataFrame({name: columns[name].reshape(-1) for name in FEATURE_NAMES})
    frame["landcover"] = frame["landcover"].astype("category")

    continuous = [name for name in FEATURE_NAMES if name not in CATEGORICAL_FEATURES]
    valid = np.isfinite(frame[continuous].to_numpy()).all(axis=1) & (landcover.reshape(-1) != 0)
    return frame, valid.reshape(shape)


def build_features(cube: xr.Dataset) -> tuple[pd.DataFrame, np.ndarray]:
    """Feature matrix for a whole cube. See `features_from_arrays`."""
    return features_from_arrays({name: np.asarray(cube[name].values) for name in BASE_VARIABLES})


def target_from_cube(cube: xr.Dataset) -> np.ndarray:
    """The training label, flattened to match the feature rows."""
    return np.asarray(cube[TARGET_VARIABLE].values, dtype="float64").reshape(-1)
