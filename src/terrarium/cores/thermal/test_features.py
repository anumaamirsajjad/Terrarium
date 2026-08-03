"""Phase 4: multi-window training frames and leakage-safe cross-validation.

In memory throughout — a synthetic multi-window cube, no Zarr and no network.

The tests that matter most here are not about accuracy. They are about whether the
cross-validation splits mean what the report says they mean: pooling seasons makes it
very easy to accidentally score a model on rows it has effectively already seen.
"""

from __future__ import annotations

import numpy as np
import pytest
import xarray as xr

from terrarium.cores.thermal.features import (
    FEATURE_NAMES,
    METEOROLOGY_VARIABLES,
    build_training_frame,
    features_from_arrays,
    meteorology_from_cube,
)
from terrarium.cores.thermal.model import (
    leave_one_window_out_cv,
    pooled_spatial_folds,
    spatial_folds,
)

# 60x60 rather than something smaller: `model.BLOCK_CELLS` is 20, so a 40x40 tile holds
# only 2x2 blocks and a 5-fold split could never populate all five folds. 3x3 blocks can.
SHAPE = (60, 60)
BUILT_UP, TREE_COVER = 50, 10

# Two summers and a winter: enough that "hold out a window" and "hold out a season" are
# different things, which a two-window fixture could not distinguish.
WINDOWS = ("2023-summer", "2023-winter", "2024-summer")
# Winter is ~20 degC cooler and more humid. The point is that meteorology, not the land
# surface, is what separates the windows.
MET_BY_WINDOW = {
    "2023-summer": (34.0, 2.5, 38.0),
    "2023-winter": (14.0, 1.2, 72.0),
    "2024-summer": (36.0, 3.1, 35.0),
}


def synthetic_multi_window_cube() -> xr.Dataset:
    """A toy tile observed in three windows.

    The land surface is *identical* in every window — only the seasonal offset differs.
    That is deliberate and it is what makes the leakage test sharp: if a fold split lets
    the same grid cell appear in both train and test via a different window, it is
    handing the model a literal duplicate of the row it is being scored on.
    """
    height, width = SHAPE
    landcover = np.full(SHAPE, BUILT_UP, dtype="uint8")
    landcover[:10, :] = TREE_COVER

    ramp = np.linspace(0.0, 0.04, width)[None, :] + np.linspace(0.0, 0.04, height)[:, None]
    ndvi = np.where(landcover == TREE_COVER, 0.65, 0.12) + ramp
    ndbi = np.where(landcover == TREE_COVER, -0.20, 0.25) - ramp
    albedo = np.where(landcover == TREE_COVER, 0.14, 0.20)
    elevation = 210.0 + ramp * 100.0

    def stack(values: np.ndarray) -> np.ndarray:
        return np.repeat(values[None, ...], len(WINDOWS), axis=0)

    # Same spatial physics every window, shifted by that window's air temperature.
    offsets = np.array([MET_BY_WINDOW[w][0] for w in WINDOWS], dtype="float64")
    lst = 25.0 * (1.0 - ndvi)[None, ...] + offsets[:, None, None]

    met = {
        name: ("time", np.array([MET_BY_WINDOW[w][i] for w in WINDOWS], dtype="float32"))
        for i, name in enumerate(METEOROLOGY_VARIABLES)
    }

    return xr.Dataset(
        {
            "ndvi": (("time", "y", "x"), stack(ndvi).astype("float32")),
            "ndbi": (("time", "y", "x"), stack(ndbi).astype("float32")),
            "albedo": (("time", "y", "x"), stack(albedo).astype("float32")),
            "elevation_m": (("time", "y", "x"), stack(elevation).astype("float32")),
            "landcover": (("y", "x"), landcover),
            "lst_c": (("time", "y", "x"), lst.astype("float32")),
            **met,
        },
        coords={
            "y": np.arange(height, dtype="float64"),
            "x": np.arange(width, dtype="float64"),
            "window": ("time", np.array(WINDOWS, dtype="<U32")),
        },
    )


@pytest.fixture(scope="module")
def cube() -> xr.Dataset:
    return synthetic_multi_window_cube()


# --------------------------------------------------------------------------------
# Meteorology as a feature
# --------------------------------------------------------------------------------


def test_meteorology_is_constant_across_the_tile(cube: xr.Dataset) -> None:
    """It varies between windows, never within one. Anything else invents structure."""
    window = cube.isel(time=0)
    arrays = {n: np.asarray(window[n].values) for n in
              ("ndvi", "ndbi", "albedo", "elevation_m", "landcover")}
    frame, _ = features_from_arrays(arrays, meteorology_from_cube(window))

    for name in METEOROLOGY_VARIABLES:
        assert frame[name].nunique() == 1, name
    assert frame["air_temp_c"].iloc[0] == pytest.approx(MET_BY_WINDOW["2023-summer"][0])


def test_meteorology_is_required_not_defaulted(cube: xr.Dataset) -> None:
    """A silent default would train against the wrong weather without any visible sign."""
    window = cube.isel(time=0)
    arrays = {n: np.asarray(window[n].values) for n in
              ("ndvi", "ndbi", "albedo", "elevation_m", "landcover")}

    with pytest.raises(ValueError, match="meteorology"):
        features_from_arrays(arrays, {"air_temp_c": 30.0})


def test_meteorology_from_cube_rejects_the_whole_cube(cube: xr.Dataset) -> None:
    """Three values where one is expected means the caller forgot `select_window`."""
    with pytest.raises(ValueError, match="select_window"):
        meteorology_from_cube(cube)


# --------------------------------------------------------------------------------
# Training frame
# --------------------------------------------------------------------------------


def test_training_frame_stacks_every_window(cube: xr.Dataset) -> None:
    training = build_training_frame(cube)

    assert training.windows == list(WINDOWS)
    assert set(training.features.columns) == set(FEATURE_NAMES)
    # Same land surface each window, so every window contributes the same usable rows.
    per_window = len(training.target) // len(WINDOWS)
    assert len(training.target) == per_window * len(WINDOWS)
    assert set(np.unique(training.window_index)) == {0, 1, 2}


def test_training_frame_can_select_a_subset(cube: xr.Dataset) -> None:
    training = build_training_frame(cube, ["2024-summer"])

    assert training.windows == ["2024-summer"]
    assert training.features["air_temp_c"].nunique() == 1
    assert training.features["air_temp_c"].iloc[0] == pytest.approx(36.0)


def test_training_frame_rows_are_all_finite(cube: xr.Dataset) -> None:
    training = build_training_frame(cube)
    continuous = [n for n in FEATURE_NAMES if n != "landcover"]

    assert np.isfinite(training.features[continuous].to_numpy()).all()
    assert np.isfinite(training.target).all()


def test_training_frame_rejects_windows_with_no_usable_rows() -> None:
    cube = synthetic_multi_window_cube()
    # Blank the target everywhere, as a fully-clouded winter would.
    cube["lst_c"].values[:] = np.nan

    with pytest.raises(ValueError, match="usable training row"):
        build_training_frame(cube)


# --------------------------------------------------------------------------------
# The leakage guard — the reason `cell_index` exists
# --------------------------------------------------------------------------------


def test_pooled_folds_hold_a_cell_out_of_every_window(cube: xr.Dataset) -> None:
    """The core Phase 4 correctness property.

    A grid cell must land in the same fold in all three windows. If it did not, the model
    would train on cell (10, 10) in 2023-summer and be scored on the very same cell in
    2024-summer — and since the land surface is identical between them, that is scoring
    on a duplicate. The MAE would look excellent and mean nothing.
    """
    training = build_training_frame(cube)
    folds = pooled_spatial_folds(SHAPE, training.cell_index)

    by_window = {
        w: dict(zip(training.cell_index[training.window_index == i],
                    folds[training.window_index == i], strict=True))
        for i, w in enumerate(training.windows)
    }

    first = by_window[training.windows[0]]
    for window in training.windows[1:]:
        assert by_window[window] == first, f"{window} assigns cells to different folds"


def test_pooled_folds_agree_with_the_single_window_assignment(cube: xr.Dataset) -> None:
    """Pooling must not change *which* block a cell belongs to, only replicate it."""
    training = build_training_frame(cube)
    pooled = pooled_spatial_folds(SHAPE, training.cell_index)
    per_cell = spatial_folds(SHAPE).reshape(-1)

    assert np.array_equal(pooled, per_cell[training.cell_index])


def test_pooled_folds_use_every_fold(cube: xr.Dataset) -> None:
    training = build_training_frame(cube)
    folds = pooled_spatial_folds(SHAPE, training.cell_index)

    assert set(np.unique(folds)) == set(range(5))


# --------------------------------------------------------------------------------
# Leave-one-window-out
# --------------------------------------------------------------------------------


def test_leave_one_window_out_holds_out_whole_windows(cube: xr.Dataset) -> None:
    training = build_training_frame(cube)
    report = leave_one_window_out_cv(
        training.features,
        training.target,
        training.window_index,
        training.windows,
        num_boost_round=40,
    )

    assert [f.label for f in report.folds] == list(WINDOWS)
    # Every fold tests exactly one window's rows and trains on the rest.
    per_window = len(training.target) // len(WINDOWS)
    for fold in report.folds:
        assert fold.n_test == per_window
        assert fold.n_train == len(training.target) - per_window


def test_held_out_window_is_harder_than_a_spatial_block(cube: xr.Dataset) -> None:
    """The gap between the two splits is the number Phase 4 exists to report.

    A held-out window's meteorology was never seen, so the model cannot place its
    seasonal offset and must fall back on the nearest window it did see. That has to cost
    more error than holding out a patch of ground in a season it knows.
    """
    from terrarium.cores.thermal.model import blocked_cv

    training = build_training_frame(cube)

    spatial = blocked_cv(
        training.features,
        training.target,
        pooled_spatial_folds(SHAPE, training.cell_index),
        num_boost_round=40,
    )
    temporal = leave_one_window_out_cv(
        training.features,
        training.target,
        training.window_index,
        training.windows,
        num_boost_round=40,
    )

    assert temporal.mae_mean > spatial.mae_mean


def test_pooled_baseline_flatters_the_model_and_per_window_does_not(cube: xr.Dataset) -> None:
    """The naive baseline must know what season it is, or the skill number is a lie.

    Pooled over windows, "predict the mean" predicts one number for a set spanning a
    34 degC summer and a 14 degC winter, so most of its error is just the seasonal
    offset. Beating that shows only that the model can tell the seasons apart - which is
    trivially true once meteorology is a feature. Grouping the baseline by window strips
    the seasonal offset out of both sides and leaves the comparison that actually matters:
    within a window, does the model beat the window's own average?
    """
    from terrarium.cores.thermal.model import blocked_cv

    training = build_training_frame(cube)
    folds = pooled_spatial_folds(SHAPE, training.cell_index)

    pooled = blocked_cv(training.features, training.target, folds, num_boost_round=40)
    per_window = blocked_cv(
        training.features,
        training.target,
        folds,
        num_boost_round=40,
        baseline_groups=training.window_index,
    )

    # Same model, same folds - only the yardstick changed.
    assert per_window.mae_mean == pytest.approx(pooled.mae_mean)
    # The per-window baseline is the harder one to beat, so it reports *less* skill.
    assert per_window.baseline_mae_mean < pooled.baseline_mae_mean
    assert per_window.skill < pooled.skill


def test_per_window_baseline_falls_back_when_the_window_is_unseen(cube: xr.Dataset) -> None:
    """A window with no training rows has no known mean, so the pooled one must stand in.

    This is exactly the leave-one-window-out case, and it is why that split does not pass
    `baseline_groups` - grouping there would be a no-op, and this pins that it is.
    """
    from terrarium.cores.thermal.model import blocked_cv

    training = build_training_frame(cube)

    grouped = blocked_cv(
        training.features,
        training.target,
        training.window_index,
        num_boost_round=40,
        n_folds=len(WINDOWS),
        labels=list(WINDOWS),
        baseline_groups=training.window_index,
    )
    ungrouped = leave_one_window_out_cv(
        training.features,
        training.target,
        training.window_index,
        training.windows,
        num_boost_round=40,
    )

    assert grouped.baseline_mae_mean == pytest.approx(ungrouped.baseline_mae_mean)
