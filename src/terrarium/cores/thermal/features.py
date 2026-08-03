"""Cube -> feature matrix for the thermal emulator.

One row per pixel, in row-major (y, x) order, so a prediction vector reshapes straight
back onto the grid. Pure: arrays in, DataFrame out.

The neighbourhood means are the highest-value feature here. Land surface temperature at a
point is driven by the surrounding few hundred metres, not by the 100 m cell alone - and
they are also what makes an intervention's cooling extend past the polygon the user drew.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import NamedTuple

import numpy as np
import pandas as pd
import xarray as xr
from scipy.ndimage import uniform_filter

# The cube variables the model reads. `lst_c` is the target, not a feature.
BASE_VARIABLES: tuple[str, ...] = ("ndvi", "ndbi", "albedo", "elevation_m", "landcover")

# Meteorology is `Dims.TIME`: one value per window, no spatial variation. It enters as a
# column that is *constant across the tile* and only varies between windows, which is
# exactly what lets one model span summer and winter instead of needing one per season.
#
# The cost of that is worth stating plainly: with N windows these columns take N distinct
# values, so a tree can use them as a window identifier. That is legitimate for
# interpolating within the seasons observed, and it is why leave-one-window-out CV is
# reported alongside the spatial one - see `model.leave_one_window_out_cv`.
METEOROLOGY_VARIABLES: tuple[str, ...] = (
    "air_temp_c",
    "wind_speed_ms",
    "relative_humidity_pct",
)

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
    *METEOROLOGY_VARIABLES,
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


def features_from_arrays(
    arrays: dict[str, np.ndarray], meteorology: Mapping[str, float]
) -> tuple[pd.DataFrame, np.ndarray]:
    """Build the feature matrix from raw (y, x) arrays plus the window's meteorology.

    Separate from `build_features` so `simulate` can perturb the arrays and rebuild -
    including the neighbourhood terms, which must be recomputed from the *perturbed*
    field rather than carried over from the baseline.

    `meteorology` is required rather than defaulted. A default would let a caller train
    or simulate against silently wrong weather, and because the columns are constant the
    mistake would not show up as a NaN or a shape error anywhere downstream.

    Returns the frame (one row per pixel) and a (y, x) boolean mask of rows whose
    features are all usable.
    """
    missing = [name for name in BASE_VARIABLES if name not in arrays]
    if missing:
        raise ValueError(f"features need cube variables that are absent: {missing}")

    missing_met = [name for name in METEOROLOGY_VARIABLES if name not in meteorology]
    if missing_met:
        raise ValueError(f"features need meteorology that is absent: {missing_met}")

    ndvi = arrays["ndvi"].astype("float64")
    ndbi = arrays["ndbi"].astype("float64")
    landcover = arrays["landcover"]
    shape = ndvi.shape

    columns: dict[str, np.ndarray] = {
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
    # Broadcast the window's scalars across the tile. Genuinely constant - this asserts
    # no spatial structure that was not measured, it just lets one model span seasons.
    for name in METEOROLOGY_VARIABLES:
        columns[name] = np.full(shape, float(meteorology[name]), dtype="float64")

    frame = pd.DataFrame({name: columns[name].reshape(-1) for name in FEATURE_NAMES})
    frame["landcover"] = frame["landcover"].astype("category")

    continuous = [name for name in FEATURE_NAMES if name not in CATEGORICAL_FEATURES]
    valid = np.isfinite(frame[continuous].to_numpy()).all(axis=1) & (landcover.reshape(-1) != 0)
    return frame, valid.reshape(shape)


def meteorology_from_cube(cube: xr.Dataset) -> dict[str, float]:
    """The window's meteorology scalars. `cube` must already be a single-window slice."""
    values: dict[str, float] = {}
    for name in METEOROLOGY_VARIABLES:
        if name not in cube:
            raise ValueError(f"cube has no meteorology variable {name!r}")
        array = np.asarray(cube[name].values)
        if array.size != 1:
            raise ValueError(
                f"{name} has {array.size} values - pass one window "
                f"(state.cube.select_window), not the whole cube"
            )
        values[name] = float(array.reshape(-1)[0])
    return values


def build_features(cube: xr.Dataset) -> tuple[pd.DataFrame, np.ndarray]:
    """Feature matrix for one window's 2-D cube. See `features_from_arrays`."""
    arrays = {name: np.asarray(cube[name].values) for name in BASE_VARIABLES}
    return features_from_arrays(arrays, meteorology_from_cube(cube))


def target_from_cube(cube: xr.Dataset) -> np.ndarray:
    """The training label, flattened to match the feature rows."""
    return np.asarray(cube[TARGET_VARIABLE].values, dtype="float64").reshape(-1)


class TrainingFrame(NamedTuple):
    """Every window stacked into one training set, row-aligned throughout.

    `window_index` and `cell_index` are what make honest cross-validation possible: the
    first groups rows by window for temporal splits, the second identifies *which grid
    cell* a row came from so a spatial split can hold the same location out of every
    window at once. Without the latter, pooling windows silently leaks - land cover
    barely changes between them, so the same pixel in another window is very nearly a
    duplicate row.
    """

    features: pd.DataFrame
    target: np.ndarray
    window_index: np.ndarray
    cell_index: np.ndarray
    windows: list[str]


def build_training_frame(cube: xr.Dataset, labels: Sequence[str] | None = None) -> TrainingFrame:
    """Stack the requested windows into a single filtered training set.

    Only usable rows survive: finite features, a real land-cover class, and a finite
    target. Windows whose meteorology is entirely missing contribute nothing and are
    reported rather than silently dropped.
    """
    from terrarium.state.cube import select_window, window_labels

    chosen = list(labels) if labels is not None else window_labels(cube)

    frames: list[pd.DataFrame] = []
    targets: list[np.ndarray] = []
    window_ids: list[np.ndarray] = []
    cell_ids: list[np.ndarray] = []
    used: list[str] = []

    for label in chosen:
        window = select_window(cube, label)
        frame, valid = build_features(window)
        target = target_from_cube(window)

        rows = valid.reshape(-1) & np.isfinite(target)
        if not rows.any():
            continue

        frames.append(frame[rows])
        targets.append(target[rows])
        window_ids.append(np.full(int(rows.sum()), len(used), dtype="int16"))
        cell_ids.append(np.nonzero(rows)[0].astype("int32"))
        used.append(label)

    if not frames:
        raise ValueError(f"no window in {chosen} yielded a usable training row")

    return TrainingFrame(
        features=pd.concat(frames, ignore_index=True),
        target=np.concatenate(targets),
        window_index=np.concatenate(window_ids),
        cell_index=np.concatenate(cell_ids),
        windows=used,
    )
