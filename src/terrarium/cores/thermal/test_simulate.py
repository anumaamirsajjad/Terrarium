"""Acceptance test for the thermal core: plant trees in a hot patch, expect cooling.

Entirely in memory - a synthetic cube and a booster trained on it inside the test. No
network, no Zarr, no fixtures on disk. That is the whole point of `cores/` being pure.
"""

from __future__ import annotations

import lightgbm as lgb
import numpy as np
import pytest
import xarray as xr

from terrarium.cores.base import CoreResult, Intervention
from terrarium.cores.thermal.features import build_features, target_from_cube
from terrarium.cores.thermal.model import blocked_cv, importances, spatial_folds, train
from terrarium.cores.thermal.simulate import simulate, tree_reference

SHAPE = (60, 60)

BUILT_UP, TREE_COVER, WATER = 50, 10, 80


def synthetic_cube() -> xr.Dataset:
    """A toy tile where LST is a clean decreasing function of vegetation.

    Deliberately obvious physics: if the core cannot recover cooling here, no amount of
    real data will save it.
    """
    height, width = SHAPE
    landcover = np.full(SHAPE, BUILT_UP, dtype="uint8")
    landcover[:12, :] = TREE_COVER  # a wooded band along the top
    landcover[:, :4] = WATER  # a river down the west edge

    is_tree = landcover == TREE_COVER
    is_water = landcover == WATER

    # A faint deterministic gradient so the model has something to split on beyond the
    # three class means.
    ramp = np.linspace(0.0, 0.04, width)[None, :] + np.linspace(0.0, 0.04, height)[:, None]

    ndvi = np.where(is_tree, 0.65, 0.12).astype("float64") + ramp
    ndvi[is_water] = -0.05
    ndbi = np.where(is_tree, -0.20, 0.25).astype("float64") - ramp
    albedo = np.where(is_tree, 0.14, 0.20).astype("float64")
    elevation = 210.0 + ramp * 100.0
    lst = 30.0 + 25.0 * (1.0 - ndvi)

    return xr.Dataset(
        {
            "ndvi": (("y", "x"), ndvi.astype("float32")),
            "ndbi": (("y", "x"), ndbi.astype("float32")),
            "albedo": (("y", "x"), albedo.astype("float32")),
            "elevation_m": (("y", "x"), elevation.astype("float32")),
            "landcover": (("y", "x"), landcover),
            "lst_c": (("y", "x"), lst.astype("float32")),
        },
        coords={"y": np.arange(height, dtype="float64"), "x": np.arange(width, dtype="float64")},
    )


@pytest.fixture(scope="module")
def fitted() -> tuple[xr.Dataset, lgb.Booster]:
    cube = synthetic_cube()
    frame, valid = build_features(cube)
    rows = valid.reshape(-1)
    target = target_from_cube(cube)
    model = train(frame[rows], target[rows], num_boost_round=150)
    return cube, model


def plant(
    cube: xr.Dataset,
    model: lgb.Booster,
    *,
    fraction: float = 0.4,
    box: tuple[int, int, int, int] = (40, 50, 40, 50),
) -> tuple[np.ndarray, CoreResult]:
    y0, y1, x0, x1 = box
    mask = np.zeros(SHAPE, dtype=bool)
    mask[y0:y1, x0:x1] = True
    result = simulate(cube, Intervention(mask=mask, canopy_fraction_added=fraction), model)
    return mask, result


def test_planting_cools_the_planted_area(fitted: tuple[xr.Dataset, lgb.Booster]) -> None:
    cube, model = fitted
    mask, result = plant(cube, model)

    assert result.stats.mean_delta_inside < 0.0
    assert np.nanmean(result.delta[mask]) < -0.5


def test_untouched_far_field_is_unchanged(fitted: tuple[xr.Dataset, lgb.Booster]) -> None:
    cube, model = fitted
    _, result = plant(cube, model)

    # Beyond the 500 m neighbourhood window nothing about the far field's feature row
    # changed, so the two predictions are bit-identical.
    far = result.delta[20:30, 20:30]
    assert np.abs(far).max() == pytest.approx(0.0, abs=1e-9)


def test_cooling_spills_past_the_polygon(fitted: tuple[xr.Dataset, lgb.Booster]) -> None:
    cube, model = fitted
    _, result = plant(cube, model)

    # One cell outside the planted box, still inside its 500 m neighbourhood window.
    assert result.delta[39, 45] < 0.0
    assert result.stats.mean_delta_spillover < 0.0


def test_water_is_not_plantable(fitted: tuple[xr.Dataset, lgb.Booster]) -> None:
    cube, model = fitted
    mask = np.zeros(SHAPE, dtype=bool)
    mask[30:40, :3] = True  # entirely inside the river
    result = simulate(cube, Intervention(mask=mask, canopy_fraction_added=0.4), model)

    assert result.stats.n_cells_changed == 0
    assert np.abs(np.nan_to_num(result.delta)).max() == pytest.approx(0.0, abs=1e-9)


def test_delta_scales_with_canopy_fraction(fitted: tuple[xr.Dataset, lgb.Booster]) -> None:
    cube, model = fitted
    _, small = plant(cube, model, fraction=0.1)
    _, large = plant(cube, model, fraction=0.5)

    assert large.stats.mean_delta_inside < small.stats.mean_delta_inside


def test_tree_reference_is_cooler_than_the_tile(
    fitted: tuple[xr.Dataset, lgb.Booster],
) -> None:
    cube, _ = fitted
    names = ("ndvi", "ndbi", "albedo", "landcover")
    arrays = {name: np.asarray(cube[name].values) for name in names}
    reference = tree_reference(arrays)

    assert reference["ndvi"] > float(np.median(arrays["ndvi"]))
    assert reference["ndbi"] < float(np.median(arrays["ndbi"]))


def test_blocked_cv_beats_the_naive_baseline(fitted: tuple[xr.Dataset, lgb.Booster]) -> None:
    cube, _ = fitted
    frame, valid = build_features(cube)
    rows = valid.reshape(-1)
    folds = spatial_folds(SHAPE, block_cells=10).reshape(-1)[rows]

    report = blocked_cv(frame[rows], target_from_cube(cube)[rows], folds, num_boost_round=100)

    assert len(report.folds) == 5
    assert report.mae_mean < report.baseline_mae_mean
    assert report.skill > 0.5


def test_spatial_folds_are_contiguous_blocks() -> None:
    folds = spatial_folds((60, 60), block_cells=10)

    # Every cell of a block shares one fold - that is what stops neighbouring pixels
    # landing on both sides of the split.
    assert len(np.unique(folds[:10, :10])) == 1
    assert set(np.unique(folds)) == set(range(5))


def test_importances_sum_to_one(fitted: tuple[xr.Dataset, lgb.Booster]) -> None:
    _, model = fitted
    gains = importances(model)

    assert sum(gains.values()) == pytest.approx(1.0)
    assert set(gains) <= {
        "ndvi",
        "ndbi",
        "albedo",
        "elevation_m",
        "landcover",
        "ndvi_mean_500m",
        "ndbi_mean_500m",
    }


def test_mask_shape_is_validated(fitted: tuple[xr.Dataset, lgb.Booster]) -> None:
    cube, model = fitted
    bad = Intervention(mask=np.ones((5, 5), dtype=bool), canopy_fraction_added=0.3)

    with pytest.raises(ValueError, match="mask"):
        simulate(cube, bad, model)
