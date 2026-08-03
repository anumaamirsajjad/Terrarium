"""Apply a tree-planting intervention and return the modelled LST delta.

Pure. The cube, the intervention and the trained booster all arrive as arguments; nothing
here reads a file, a clock, or config.

Two rules that are easy to get wrong and expensive to get wrong quietly:

1. **Difference two predictions, never prediction-minus-observed.** Otherwise the model's
   residual leaks into every scenario and untouched pixels show change that is really
   just training error.
2. **Return the whole-tile field, not the masked region.** The neighbourhood features
   mean cooling genuinely extends past the polygon - that spillover is real physics and
   deleting it would understate the intervention.
"""

from __future__ import annotations

import lightgbm as lgb
import numpy as np
import xarray as xr

from terrarium.cores.base import CoreResult, Intervention, summarise_delta
from terrarium.cores.thermal.features import (
    BASE_VARIABLES,
    features_from_arrays,
    meteorology_from_cube,
)
from terrarium.cores.thermal.model import predict

# WorldCover class codes. Tree cover is the state we move planted cells toward; water is
# where you cannot plant.
TREE_COVER_CLASS = 10
BUILT_UP_CLASS = 50
WATER_CLASS = 80
NODATA_CLASS = 0

# NDVI of bare ground. Used only as the zero-point of the canopy-fraction proxy that caps
# how much more canopy a cell can take - a cell already reading like closed woodland has
# nothing left to plant.
NDVI_BARE = 0.10

# The features an added canopy fraction moves. `landcover` is deliberately *not* among
# them: adding 30 % canopy to a built-up block leaves it a built-up block, and flipping
# the class as well would count the same cooling twice - once through the class and once
# through NDVI. See the double-counting note in the Phase 2 plan.
PERTURBED_FEATURES: tuple[str, ...] = ("ndvi", "ndbi", "albedo")

RESULT_VARIABLE = "lst_c"
RESULT_UNITS = "degC"


def tree_reference(arrays: dict[str, np.ndarray]) -> dict[str, float]:
    """Median feature values of this tile's own tree-cover pixels.

    Observed, not assumed: what "a tree pixel in Lahore in May" looks like is a property
    of this tile, and hardcoding a literature value would put the model's target outside
    the range it was trained on.
    """
    is_tree = arrays["landcover"] == TREE_COVER_CLASS
    reference: dict[str, float] = {}

    for name in PERTURBED_FEATURES:
        values = arrays[name].astype("float64")
        usable = is_tree & np.isfinite(values)
        if not usable.any():
            raise ValueError(f"tile has no valid tree-cover pixels to derive {name} from")
        reference[name] = float(np.median(values[usable]))

    return reference


def effective_fraction(arrays: dict[str, np.ndarray], intervention: Intervention) -> np.ndarray:
    """Per-cell canopy fraction actually added, after capping.

    Capped two ways: by what is left to plant (NDVI as a canopy proxy) and by where you
    can plant at all. Requesting +30 % canopy on the Ravi should add nothing.
    """
    ndvi = arrays["ndvi"].astype("float64")
    landcover = arrays["landcover"]

    tree_ndvi = tree_reference(arrays)["ndvi"]
    span = max(tree_ndvi - NDVI_BARE, 1e-6)
    current_canopy = np.clip((ndvi - NDVI_BARE) / span, 0.0, 1.0)

    plantable = (
        intervention.mask
        & np.isfinite(ndvi)
        & (landcover != WATER_CLASS)
        & (landcover != NODATA_CLASS)
    )
    headroom = np.where(plantable, 1.0 - current_canopy, 0.0)
    return np.minimum(intervention.canopy_fraction_added, headroom)


def perturb(arrays: dict[str, np.ndarray], fraction: np.ndarray) -> dict[str, np.ndarray]:
    """Move each cell's features `fraction` of the way toward the tree reference."""
    reference = tree_reference(arrays)
    perturbed = dict(arrays)

    for name in PERTURBED_FEATURES:
        values = arrays[name].astype("float64")
        perturbed[name] = values + fraction * (reference[name] - values)

    return perturbed


def tree_built_contrast(cube: xr.Dataset) -> float:
    """This window's observed median LST gap between built-up and tree-cover pixels.

    The **ceiling on what any planting can buy**: converting a cell entirely to tree
    cover cannot cool it by more than the difference the data actually contains. Report
    a modelled delta next to this number, never on its own - Phase 2 measured 2.60 degC
    here against a literature expectation of 3-6 degC, and quoting the literature would
    have made a correct model look broken.

    A property of *this window*, not of the tile: summer reads 2.6-2.8 degC and winter
    0.3-0.8 degC, so a single cached value would misdescribe half the cube.

    Positive means built-up is hotter, which is the expected sign.
    """
    lst = np.asarray(cube[RESULT_VARIABLE].values, dtype="float64")
    landcover = np.asarray(cube["landcover"].values)

    built = (landcover == BUILT_UP_CLASS) & np.isfinite(lst)
    trees = (landcover == TREE_COVER_CLASS) & np.isfinite(lst)
    if not built.any() or not trees.any():
        raise ValueError("window has no valid built-up or tree-cover pixels to contrast")

    return float(np.median(lst[built]) - np.median(lst[trees]))


def simulate(
    cube: xr.Dataset, intervention: Intervention, model: lgb.Booster
) -> CoreResult:
    """Predict the LST change caused by `intervention`. Whole tile, degrees Celsius."""
    arrays = {name: np.asarray(cube[name].values) for name in BASE_VARIABLES}
    shape = arrays["ndvi"].shape

    if intervention.mask.shape != shape:
        raise ValueError(f"intervention mask {intervention.mask.shape} != cube grid {shape}")

    fraction = effective_fraction(arrays, intervention)

    # The same weather on both sides. Planting trees does not change the synoptic
    # conditions the window was composited under, and letting meteorology differ between
    # baseline and scenario would attribute a seasonal offset to the intervention.
    meteorology = meteorology_from_cube(cube)

    baseline_frame, valid = features_from_arrays(arrays, meteorology)
    # Rebuilt, not patched: the 500 m neighbourhood terms have to come from the perturbed
    # field, and forgetting that is exactly what silently removes the spillover.
    scenario_frame, _ = features_from_arrays(perturb(arrays, fraction), meteorology)

    delta = np.full(shape, np.nan, dtype="float32")
    rows = valid.reshape(-1)
    if rows.any():
        change = predict(model, scenario_frame[rows]) - predict(model, baseline_frame[rows])
        delta.reshape(-1)[rows] = change.astype("float32")

    return CoreResult(
        variable=RESULT_VARIABLE,
        units=RESULT_UNITS,
        delta=delta,
        stats=summarise_delta(delta, fraction > 0),
    )
