"""Hindcast validation: did the model get the effect of a *change* right?

Every number the thermal core has produced so far answers a different question. Blocked
CV asks "can it predict LST somewhere unseen?" — spatial generalisation. Leave-one-window-
out asks "can it reach a date it has never seen?" — temporal generalisation. Neither asks
the question the product actually rests on: **when the land surface changes, does the
modelled temperature change with it, by the right amount?** Space-for-time substitution
assumes it does. This module is where that assumption gets tested.

The procedure (D7): no target site is known, so find one by change detection over the
archive, train strictly on windows *before* the change, predict the post-change field, and
compare against observed Landsat ST_B10.

### Why raw MAE cannot answer this on its own

Leave-one-window-out measured ~2.9 °C of error for reaching an unseen date. A hindcast
window is exactly that, so its MAE inherits a whole-window offset that has nothing to do
with the change: get the year's synoptic conditions slightly wrong and every cell moves
together. That offset would swamp the tenths of a degree a real greening buys.

The estimator that survives it is a **difference-in-differences**. Changed and unchanged
cells sit in the same window and share the same offset, so comparing the model's bias at
changed cells against its bias at unchanged cells cancels it:

    change_effect_error = bias(changed) - bias(unchanged)

If the model tracked the change correctly, the two biases are equal and this is ~0 —
*whatever* the window-level offset happened to be. That is the number this module is built
around; MAE and spatial R² are reported alongside because the plan asks for them, not
because they settle the question.

### The control group is matched, because an unmatched one is confounded

Comparing changed cells against *every* unchanged cell on the tile assumes the two groups
are otherwise alike, and they are not. Land that greens is land that had room to green:
low baseline NDVI, often on the urban fringe. If the model's bias varies with land cover
or greenness — and for a gradient-boosted model on 40,602 cells it certainly does — then a
raw difference of biases measures that covariate gap as much as it measures mistracking.

So the estimator is computed **within strata** of land-cover class x baseline-NDVI decile,
and the per-stratum differences are averaged weighted by how the *changed* cells are
distributed across strata. A stratum with no unchanged cells to compare against is dropped
and counted rather than silently extrapolated over.

Both the raw and matched numbers are reported. The gap between them is how much of the
apparent effect was confounding.

Baseline NDVI is matched on; baseline **LST** deliberately is not. Matching on a noisy
pre-period measurement of the outcome itself induces regression to the mean, which would
manufacture an effect out of composite noise — exactly the noise this tile has most of.

Pure, like the rest of `cores/`: cube and arrays in, numbers out, no I/O.

Unlike `simulate`, the model is **not** an argument here. "Train strictly on before" is the
entire discipline being tested, so leaving it to a caller would make the one mistake that
invalidates the result — training on data from after the change — an easy accident. The
booster is fitted inside, from labels the caller names.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import xarray as xr
from pydantic import BaseModel, ConfigDict
from scipy.ndimage import label

from terrarium.cores.thermal.features import (
    build_features,
    build_training_frame,
    target_from_cube,
)
from terrarium.cores.thermal.model import predict, train
from terrarium.state.cube import select_window

# Robust spread multiplier for the change threshold. A cell counts as changed when its
# NDVI shift exceeds the tile's own median shift by this many robust standard deviations,
# rather than by a value from a paper: the whole tile greens or browns a little between
# any two years, and only the excess over that common drift is a site-specific change.
DEFAULT_SIGMA = 3.0
# 1 / Phi^-1(0.75). Converts a median absolute deviation into a standard-deviation
# equivalent for a normal distribution, so `DEFAULT_SIGMA` means what it usually means.
MAD_TO_SIGMA = 1.4826
# A patch smaller than this is not resolvable as a site at 100 m - it is a handful of
# cells that could as easily be co-registration jitter between two composites.
MIN_SITE_CELLS = 9
# Baseline-NDVI strata for matching controls. Ten is enough to separate bare ground from
# closed canopy without splitting the tile so finely that strata run out of controls.
NDVI_MATCH_BINS = 10


class ChangeSite(BaseModel):
    """One contiguous patch of sustained NDVI change: a hindcast candidate."""

    model_config = ConfigDict(frozen=True)

    n_cells: int
    mean_ndvi_change: float
    # Centroid in the cube's own projected CRS. Converting to lon/lat needs a CRS, which
    # is the caller's business - `cores/` does not import the grid definition.
    centre_x: float
    centre_y: float
    greening: bool


class ChangeField(BaseModel):
    """The per-cell NDVI shift between two sets of windows, and what counts as changed."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    change: np.ndarray
    # Every cell over the threshold, isolated specks included. A robust 3-sigma cutoff
    # flags ~0.3 % of an unchanged tile by construction - that is what 3 sigma means -
    # so this is the raw signal, not the answer.
    changed: np.ndarray
    # Cells belonging to a patch large enough to be a site. This is what a hindcast
    # scores against: a scattering of single cells at the tail of a noise distribution
    # would dilute the estimator with cells where nothing happened.
    site_mask: np.ndarray
    threshold: float
    median_drift: float
    sites: list[ChangeSite]

    @property
    def n_changed(self) -> int:
        return int(self.changed.sum())

    @property
    def n_in_sites(self) -> int:
        return int(self.site_mask.sum())


class HindcastScore(BaseModel):
    """How well predictions matched observation over one set of cells."""

    model_config = ConfigDict(frozen=True)

    n_cells: int
    mae: float
    # Signed, and the one that matters: the difference between two biases is the estimator.
    bias: float
    spatial_r2: float
    # The same two numbers after subtracting the single window-level bias. A model that
    # cannot reach an unseen year's absolute temperature can still rank the tile
    # correctly, and those are different failures with different fixes - one needs a
    # longer archive, the other needs better features. Raw MAE and R2 cannot tell them
    # apart, because a constant offset wrecks both.
    mae_debiased: float
    spatial_r2_debiased: float


class HindcastReport(BaseModel):
    """A full hindcast: train before, predict after, compare against observation."""

    model_config = ConfigDict(frozen=True)

    before_windows: list[str]
    after_window: str
    n_changed_cells: int
    change_threshold: float

    overall: HindcastScore
    changed: HindcastScore
    unchanged: HindcastScore

    # Did the ground actually do anything thermally? If the observed contrast is ~0 the
    # test is uninformative no matter how good the model looks, and saying so is the
    # difference between validation and theatre.
    observed_lst_change_changed: float
    observed_lst_change_unchanged: float

    # The same two effects, but with controls matched on land cover and baseline NDVI.
    # These are the ones to quote: the raw pair above compares greened land against the
    # whole tile, which differs from it in exactly the ways that predict temperature.
    matched_effect_error: MatchedDifference
    matched_observed_effect: MatchedDifference

    @property
    def observed_change_effect(self) -> float:
        """Observed ΔLST at changed cells, net of what unchanged cells did anyway."""
        return self.observed_lst_change_changed - self.observed_lst_change_unchanged

    @property
    def change_effect_error(self) -> float:
        """The headline. Model bias at changed cells minus bias at unchanged cells.

        Near zero means the model tracked the change as well as it tracks anything in
        this window - the window-level offset cancels between the two groups. Negative
        means it over-cooled the changed cells; positive, under-cooled.
        """
        return self.changed.bias - self.unchanged.bias


def _median_over_windows(cube: xr.Dataset, labels: Sequence[str], name: str) -> np.ndarray:
    """Median of one variable across several windows, per cell.

    Median over *several* windows rather than one is what makes a detected change
    "sustained": a single dry summer moves NDVI across the whole tile, and one year
    either side would report that as change everywhere.
    """
    stack = np.stack(
        [np.asarray(select_window(cube, label)[name].values, dtype="float64") for label in labels]
    )
    return np.asarray(np.nanmedian(stack, axis=0))


def detect_change(
    cube: xr.Dataset,
    before: Sequence[str],
    after: Sequence[str],
    *,
    sigma: float = DEFAULT_SIGMA,
    min_site_cells: int = MIN_SITE_CELLS,
) -> ChangeField:
    """Find cells whose NDVI shifted sustainedly between two sets of windows.

    The threshold is derived from this tile's own distribution of shifts, not from a
    literature value: between any two periods the whole tile drifts a little with rainfall
    and phenology, and only the excess over that common drift is a site-specific change.
    A hardcoded 0.2 would report the entire tile in a wet year and nothing in a dry one.
    """
    if not before or not after:
        raise ValueError("hindcast needs at least one window on each side of the change")

    change = _median_over_windows(cube, after, "ndvi") - _median_over_windows(
        cube, before, "ndvi"
    )

    finite = np.isfinite(change)
    if not finite.any():
        raise ValueError("no cell has a finite NDVI change; check the windows requested")

    drift = float(np.median(change[finite]))
    spread = MAD_TO_SIGMA * float(np.median(np.abs(change[finite] - drift)))
    if spread <= 0.0:
        # The MAD collapses to zero when more than half the tile shares one exact value -
        # a constant composite, or a quantised source. The threshold would then be 0 and
        # every cell with any deviation at all would read as a change site.
        spread = float(np.std(change[finite]))
    threshold = sigma * spread

    changed = finite & (np.abs(change - drift) > threshold)
    sites, site_mask = _sites(change, changed, cube, min_site_cells)

    return ChangeField(
        change=change,
        changed=changed,
        site_mask=site_mask,
        threshold=threshold,
        median_drift=drift,
        sites=sites,
    )


def _sites(
    change: np.ndarray, changed: np.ndarray, cube: xr.Dataset, min_cells: int
) -> tuple[list[ChangeSite], np.ndarray]:
    """Contiguous patches of change, largest first, and the mask of cells inside them.

    Greening and browning are labelled separately: a patch that is half new parkland and
    half new car park is not one site, and averaging the two would report no change at
    all.
    """
    xs = np.asarray(cube["x"].values, dtype="float64")
    ys = np.asarray(cube["y"].values, dtype="float64")

    sites: list[ChangeSite] = []
    mask = np.zeros(change.shape, dtype=bool)

    for greening in (True, False):
        signed = changed & ((change > 0) if greening else (change < 0))
        # 8-connectivity: a diagonal step is still the same patch of ground.
        components, n = label(signed, structure=np.ones((3, 3), dtype=int))
        for index in range(1, n + 1):
            rows, cols = np.nonzero(components == index)
            if rows.size < min_cells:
                continue
            mask[rows, cols] = True
            sites.append(
                ChangeSite(
                    n_cells=int(rows.size),
                    mean_ndvi_change=float(change[rows, cols].mean()),
                    centre_x=float(xs[cols].mean()),
                    centre_y=float(ys[rows].mean()),
                    greening=greening,
                )
            )

    return sorted(sites, key=lambda s: s.n_cells, reverse=True), mask


def _strata(
    landcover: np.ndarray, baseline_ndvi: np.ndarray, n_bins: int = NDVI_MATCH_BINS
) -> np.ndarray:
    """Stratum id per cell: land-cover class crossed with baseline-NDVI quantile bin.

    Quantile edges rather than fixed ones, so the bins carry roughly equal numbers of
    cells whatever this tile's NDVI distribution looks like. Cells with no usable
    baseline get -1 and are excluded from matching entirely.
    """
    usable = np.isfinite(baseline_ndvi)
    strata = np.full(baseline_ndvi.shape, -1, dtype="int64")
    if not usable.any():
        return strata

    edges = np.quantile(baseline_ndvi[usable], np.linspace(0.0, 1.0, n_bins + 1)[1:-1])
    bins = np.digitize(baseline_ndvi[usable], edges)
    strata[usable] = landcover[usable].astype("int64") * (n_bins + 1) + bins
    return strata


class MatchedDifference(BaseModel):
    """A group difference computed within strata, with its support reported."""

    model_config = ConfigDict(frozen=True)

    value: float
    matched_cells: int
    # Changed cells whose stratum held no control to compare against. A large number here
    # means the two groups barely overlap and the matched figure rests on a minority of
    # the sites - which is a reason to distrust it, so it is reported rather than hidden.
    unmatched_cells: int


def _matched_difference(
    values: np.ndarray, changed: np.ndarray, unchanged: np.ndarray, strata: np.ndarray
) -> MatchedDifference:
    """Mean changed-minus-unchanged difference, averaged within strata.

    Weighted by how the *changed* cells fall across strata, so the answer is "what is the
    difference for land like the land that actually changed" rather than "for land like
    the tile as a whole".
    """
    total = 0.0
    weight = 0.0
    unmatched = 0

    for stratum in np.unique(strata[changed]):
        if stratum < 0:
            continue
        here = strata == stratum
        treated, control = changed & here, unchanged & here
        n = int(treated.sum())
        if n == 0:
            continue
        if not control.any():
            unmatched += n
            continue
        total += n * (float(values[treated].mean()) - float(values[control].mean()))
        weight += n

    return MatchedDifference(
        value=total / weight if weight else float("nan"),
        matched_cells=int(weight),
        unmatched_cells=unmatched,
    )


def _score(predicted: np.ndarray, observed: np.ndarray) -> HindcastScore:
    """MAE, signed bias, and spatial R², raw and after removing the window-level offset."""
    residual = predicted - observed
    variance = float(((observed - observed.mean()) ** 2).sum())

    def r2(values: np.ndarray) -> float:
        # R² against the *spatial* mean of this window: "does it explain where the tile
        # is hot", not "does it beat predicting nothing". Undefined for a constant field.
        return 1.0 - float((values**2).sum()) / variance if variance > 0 else float("nan")

    centred = residual - residual.mean()
    return HindcastScore(
        n_cells=int(observed.size),
        mae=float(np.abs(residual).mean()),
        bias=float(residual.mean()),
        spatial_r2=r2(residual),
        mae_debiased=float(np.abs(centred).mean()),
        spatial_r2_debiased=r2(centred),
    )


def run_hindcast(
    cube: xr.Dataset,
    before: Sequence[str],
    after_window: str,
    field: ChangeField,
    *,
    num_boost_round: int | None = None,
) -> HindcastReport:
    """Train on `before`, predict `after_window`, and score against observed ST_B10.

    `after_window` must not appear in `before`. That is the one rule whose violation
    would invalidate everything downstream, so it is checked rather than documented.
    """
    if after_window in before:
        raise ValueError(
            f"{after_window!r} is in the training windows - a hindcast that trains on "
            "the window it predicts measures nothing"
        )

    training = build_training_frame(cube, labels=list(before))
    model = (
        train(training.features, training.target, num_boost_round=num_boost_round)
        if num_boost_round is not None
        else train(training.features, training.target)
    )

    window = select_window(cube, after_window)
    features, valid = build_features(window)
    observed = target_from_cube(window)

    rows = valid.reshape(-1) & np.isfinite(observed)
    if not rows.any():
        raise ValueError(f"{after_window} has no usable cell to score against")

    predicted = np.full(observed.shape, np.nan)
    predicted[rows] = predict(model, features[rows])

    # Sites, not raw flagged cells. The control group excludes *every* flagged cell
    # though, including the specks that did not make a site: a cell that may have changed
    # does not belong in a group defined by not having changed.
    changed = field.site_mask.reshape(-1) & rows
    unchanged = (~field.changed.reshape(-1)) & rows
    if not changed.any():
        raise ValueError(
            "no change site survived masking; nothing to validate against - either "
            "nothing changed on this tile or the threshold is too strict"
        )

    before_lst = _median_over_windows(cube, before, "lst_c").reshape(-1)
    after_lst = observed

    def observed_shift(cells: np.ndarray) -> float:
        usable = cells & np.isfinite(before_lst)
        if not usable.any():
            return float("nan")
        return float((after_lst[usable] - before_lst[usable]).mean())

    # Matched controls. Strata are built from land cover and *baseline* NDVI - the state
    # before anything changed - so a cell's stratum is not itself a consequence of the
    # change being measured.
    strata = _strata(
        np.asarray(cube["landcover"].values).reshape(-1),
        _median_over_windows(cube, before, "ndvi").reshape(-1),
    )
    residual = predicted - observed
    shift = after_lst - before_lst
    shift_usable = np.isfinite(before_lst) & np.isfinite(after_lst)

    return HindcastReport(
        before_windows=list(before),
        after_window=after_window,
        n_changed_cells=int(changed.sum()),
        change_threshold=field.threshold,
        overall=_score(predicted[rows], observed[rows]),
        changed=_score(predicted[changed], observed[changed]),
        unchanged=_score(predicted[unchanged], observed[unchanged]),
        observed_lst_change_changed=observed_shift(changed),
        observed_lst_change_unchanged=observed_shift(unchanged),
        matched_effect_error=_matched_difference(residual, changed, unchanged, strata),
        matched_observed_effect=_matched_difference(
            shift, changed & shift_usable, unchanged & shift_usable, strata
        ),
    )
