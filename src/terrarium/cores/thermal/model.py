"""Train and evaluate the LightGBM land-surface-temperature emulator.

Pure: data in, booster out. Nothing here opens a file or reads config - persistence of
the artefact belongs to `scripts/train_thermal.py`, outside the core.
"""

from __future__ import annotations

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
    # to the number it has to beat.
    baseline_mae: float


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
    """Predict LST for every row. Column order is pinned to `FEATURE_NAMES`."""
    return np.asarray(model.predict(features[list(FEATURE_NAMES)]), dtype="float64")


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


def blocked_cv(
    features: pd.DataFrame,
    target: np.ndarray,
    folds: np.ndarray,
    *,
    params: dict[str, Any] | None = None,
    num_boost_round: int = DEFAULT_ROUNDS,
    n_folds: int = N_FOLDS,
) -> CVReport:
    """Run spatially blocked CV. `features`, `target` and `folds` are row-aligned."""
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
        naive = float(target[train_rows].mean())

        scores.append(
            FoldScore(
                fold=fold,
                n_train=int(train_rows.sum()),
                n_test=int(test.sum()),
                mae=float(np.abs(predicted - target[test]).mean()),
                baseline_mae=float(np.abs(naive - target[test]).mean()),
            )
        )

    if not scores:
        raise ValueError("no fold had both training and test rows")
    return CVReport(folds=scores)


def importances(model: lgb.Booster) -> dict[str, float]:
    """Gain-based feature importance, normalised to sum to 1.

    Worth reading after the first train: if `landcover` and `ndvi` both carry heavy gain,
    an intervention that moves both may be counting the same cooling twice.
    """
    gains = np.asarray(model.feature_importance(importance_type="gain"), dtype="float64")
    total = gains.sum() or 1.0
    return dict(zip(model.feature_name(), (gains / total).tolist(), strict=True))
