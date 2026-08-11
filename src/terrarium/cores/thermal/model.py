"""Train and evaluate the LightGBM land-surface-temperature emulator.

Pure: data in, booster out. Nothing here opens a file or reads config - persistence of
the artefact belongs to `scripts/train_thermal.py`, outside the core.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict

from terrarium.cores.thermal.features import CATEGORICAL_FEATURES, FEATURE_NAMES

# Deliberately small. 40k pixels of a single date is not much data, and the point of the
# emulator is a smooth response to land cover, not the last 0.05 degC of training fit.
DEFAULT_PARAMS: dict[str, Any] = {
    "objective": "regression_l1",  # MAE - degrees, and robust to the odd bad pixel
    "learning_rate": 0.05,
    "num_leaves": 31,
    "min_data_in_leaf": 100,
    "feature_fraction": 0.9,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "verbosity": -1,
    "seed": 0,
    "deterministic": True,
}
DEFAULT_ROUNDS = 400

# ~2 km at 100 m. Neighbouring 100 m pixels are strongly autocorrelated, so a random
# pixel split puts near-duplicates on both sides and reports a flattering MAE that means
# nothing. Blocks big enough to exceed that correlation length are the honest split.
BLOCK_CELLS = 20
N_FOLDS = 5


class FoldScore(BaseModel):
    model_config = ConfigDict(frozen=True)

    fold: int
    n_train: int
    n_test: int
    mae: float
    # Predicting the training fold's mean everywhere. An MAE only means something next
    # to the number it has to beat. With `baseline_groups`, the mean is taken *within
    # the row's window* - see `blocked_cv`.
    baseline_mae: float
    # What was held out, when the folds mean something nameable (a window label). None
    # for spatial blocks, which have no name worth printing.
    label: str | None = None


class CVReport(BaseModel):
    """Spatially blocked cross-validation result.

    This answers *"can the model predict LST somewhere it has not seen?"* - spatial
    generalisation. It does **not** answer *"can it predict what happens after a
    change?"* Only the Phase 7 hindcast answers that. Label it as a placeholder wherever
    it is shown.
    """

    model_config = ConfigDict(frozen=True)

    folds: list[FoldScore]

    @property
    def mae_mean(self) -> float:
        return float(np.mean([f.mae for f in self.folds]))

    @property
    def mae_std(self) -> float:
        return float(np.std([f.mae for f in self.folds]))

    @property
    def baseline_mae_mean(self) -> float:
        return float(np.mean([f.baseline_mae for f in self.folds]))

    @property
    def skill(self) -> float:
        """Fraction of the naive baseline's error removed. 0 = no better than the mean."""
        return 1.0 - self.mae_mean / self.baseline_mae_mean if self.baseline_mae_mean else 0.0


def train(
    features: pd.DataFrame,
    target: np.ndarray,
    *,
    params: dict[str, Any] | None = None,
    num_boost_round: int = DEFAULT_ROUNDS,
) -> lgb.Booster:
    """Fit a booster on already-filtered rows."""
    dataset = lgb.Dataset(
        features[list(FEATURE_NAMES)],
        label=target,
        categorical_feature=list(CATEGORICAL_FEATURES),
        free_raw_data=False,
    )
    return lgb.train(params or DEFAULT_PARAMS, dataset, num_boost_round=num_boost_round)


def predict(model: lgb.Booster, features: pd.DataFrame) -> np.ndarray:
    """Predict LST for every row. Column order is pinned to `FEATURE_NAMES`.

    `num_threads=1`: left at LightGBM's default, it spawns one thread per CPU core
    `os.cpu_count()` reports - which on a cgroup-limited container (Render's free tier
    included) is routinely the *host's* core count, not the container's actual quota.
    Oversubscribing a fractional-vCPU container turns a sub-second prediction into
    several seconds of context-switching. 40,602 rows is nowhere near where
    multithreading would have paid for itself anyway.
    """
    return np.asarray(
        model.predict(features[list(FEATURE_NAMES)], num_threads=1), dtype="float64"
    )


def spatial_folds(
    shape: tuple[int, int],
    *,
    block_cells: int = BLOCK_CELLS,
    n_folds: int = N_FOLDS,
    seed: int = 0,
) -> np.ndarray:
    """Assign every grid cell to a CV fold, in contiguous square blocks.

    Folded rather than held out one block at a time: the tile is 74 % built-up but also
    holds the Ravi, the canal, cropland and an airport, so a single block can land almost
    entirely on water and report an error that describes nothing.
    """
    height, width = shape
    ys, xs = np.meshgrid(np.arange(height), np.arange(width), indexing="ij")
    blocks = (ys // block_cells) * (width // block_cells + 1) + (xs // block_cells)

    shuffled = np.random.default_rng(seed).permutation(np.unique(blocks))
    lookup = np.zeros(int(blocks.max()) + 1, dtype="int16")
    lookup[shuffled] = np.arange(len(shuffled), dtype="int16") % n_folds
    return np.asarray(lookup[blocks], dtype="int16")


def _naive_prediction(
    target: np.ndarray,
    train_rows: np.ndarray,
    test: np.ndarray,
    groups: np.ndarray | None,
) -> np.ndarray:
    """The baseline every fold has to beat: predict a mean and nothing else.

    Without `groups` that is the pooled training mean. Across multiple windows that is
    a straw man - it is one number for a set spanning 44 degC summers and 22 degC
    winters, so its MAE is ~10 degC and any model looks brilliant next to it. Beating it
    demonstrates only that the model can tell the seasons apart.

    With `groups` (pass `window_index`) the baseline instead predicts *that window's own
    training mean*, which is a real forecaster: "it is summer, so expect the summer
    average". Skill against it is the fraction of the **within-window** spatial variation
    explained, and is directly comparable to the single-window numbers of Phases 2 and 3.

    A group with no training rows - which is every test row under
    leave-one-window-out - falls back to the pooled mean, because a window the model
    never saw is a window whose mean is genuinely unknown.
    """
    pooled = float(target[train_rows].mean())
    if groups is None:
        return np.full(int(test.sum()), pooled, dtype="float64")

    means: dict[int, float] = {}
    for group in np.unique(groups[train_rows]):
        means[int(group)] = float(target[train_rows & (groups == group)].mean())
    return np.array([means.get(int(g), pooled) for g in groups[test]], dtype="float64")


def blocked_cv(
    features: pd.DataFrame,
    target: np.ndarray,
    folds: np.ndarray,
    *,
    params: dict[str, Any] | None = None,
    num_boost_round: int = DEFAULT_ROUNDS,
    n_folds: int = N_FOLDS,
    labels: Sequence[str] | None = None,
    baseline_groups: np.ndarray | None = None,
) -> CVReport:
    """Run cross-validation over a precomputed fold assignment.

    `features`, `target` and `folds` are row-aligned. The fold *meaning* is the caller's
    to choose: `pooled_spatial_folds` for spatial generalisation, `window_index` straight
    from the training frame for leave-one-window-out.

    `baseline_groups` sharpens the naive comparison on a multi-window frame - pass
    `window_index`. See `_naive_prediction` for why the default is too easy to beat.
    """
    scores: list[FoldScore] = []

    for fold in range(n_folds):
        test = folds == fold
        train_rows = ~test
        if not test.any() or not train_rows.any():
            continue

        booster = train(
            features[train_rows],
            target[train_rows],
            params=params,
            num_boost_round=num_boost_round,
        )
        predicted = predict(booster, features[test])
        naive = _naive_prediction(target, train_rows, test, baseline_groups)

        scores.append(
            FoldScore(
                fold=fold,
                n_train=int(train_rows.sum()),
                n_test=int(test.sum()),
                mae=float(np.abs(predicted - target[test]).mean()),
                baseline_mae=float(np.abs(naive - target[test]).mean()),
                label=labels[fold] if labels is not None and fold < len(labels) else None,
            )
        )

    if not scores:
        raise ValueError("no fold had both training and test rows")
    return CVReport(folds=scores)


def pooled_spatial_folds(
    shape: tuple[int, int],
    cell_index: np.ndarray,
    *,
    block_cells: int = BLOCK_CELLS,
    n_folds: int = N_FOLDS,
    seed: int = 0,
) -> np.ndarray:
    """Spatial folds for a *multi-window* frame, holding each block out of every window.

    This is the whole reason `TrainingFrame` carries `cell_index`. Assigning folds per
    row would put grid cell (100, 100) from 2024-summer in training and the same cell
    from 2023-summer in test. Land cover, elevation and the neighbourhood terms are
    nearly identical between windows, so that is a near-duplicate row across the split -
    the model looks up the answer instead of generalising, and the reported MAE flatters
    it. Keying the fold off the cell rather than the row closes that.
    """
    per_cell = spatial_folds(shape, block_cells=block_cells, n_folds=n_folds, seed=seed)
    return np.asarray(per_cell.reshape(-1)[cell_index], dtype="int16")


def leave_one_window_out_cv(
    features: pd.DataFrame,
    target: np.ndarray,
    window_index: np.ndarray,
    windows: Sequence[str],
    *,
    params: dict[str, Any] | None = None,
    num_boost_round: int = DEFAULT_ROUNDS,
) -> CVReport:
    """Hold out one whole window at a time — can the model reach an unseen date?

    This is the harder question and the one multi-date training exists to answer. It is
    also where the meteorology columns stop helping: held-out windows carry values the
    model never saw, so a tree can only fall back on the nearest window it did see.
    Expect a worse MAE here than the spatial split, and treat *that gap* as the honest
    measure of how much the model is leaning on cross-sectional contrast alone.

    Still not the hindcast (Phase 7): every window here is a different *time*, not a
    different *land surface*. Nothing has actually changed on the ground between them.

    The baseline here is necessarily the pooled training mean: the held-out window
    contributes no training rows, so its own mean is exactly the thing that is unknown.
    That is why `baseline_groups` is not passed - it would be a no-op.
    """
    return blocked_cv(
        features,
        target,
        window_index,
        params=params,
        num_boost_round=num_boost_round,
        n_folds=len(windows),
        labels=list(windows),
    )


def importances(model: lgb.Booster) -> dict[str, float]:
    """Gain-based feature importance, normalised to sum to 1.

    Worth reading after the first train: if `landcover` and `ndvi` both carry heavy gain,
    an intervention that moves both may be counting the same cooling twice.
    """
    gains = np.asarray(model.feature_importance(importance_type="gain"), dtype="float64")
    total = gains.sum() or 1.0
    return dict(zip(model.feature_name(), (gains / total).tolist(), strict=True))
