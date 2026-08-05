"""Hindcast validation, on synthetic cubes with a known planted answer.

The point of building the cube here rather than loading one: a hindcast is only
meaningful if we know what the ground truly did, and on real data we never do. These
construct a tile where the change and its thermal consequence are both exactly known, so
a wrong estimator shows up as a wrong number rather than a plausible one.

Nothing here touches the network or the disk.
"""

from __future__ import annotations

import numpy as np
import pytest
import xarray as xr

from terrarium.cores.thermal.hindcast import (
    MIN_SITE_CELLS,
    detect_change,
    run_hindcast,
)

WINDOWS = ["2020-summer", "2021-summer", "2022-summer", "2023-summer"]
SHAPE = (40, 40)
# Where the change happens. Comfortably larger than MIN_SITE_CELLS so it survives the
# site filter, and away from the edge so the 5x5 neighbourhood terms stay clean.
PATCH = (slice(10, 20), slice(10, 20))


# LST falls this many degrees per unit NDVI, everywhere and in every window. The model
# can only learn this from the *before* windows, which is the whole point - so NDVI has
# to vary across space there, not just at the patch that later changes.
NDVI_TO_LST = -10.0
NDVI_GREENING = 0.4
# Both ends of the patch's greening sit inside the tile-wide NDVI range, so the model is
# interpolating a relationship it has seen rather than reaching past its training data.
NDVI_PATCH_BEFORE = 0.15


def _cube(
    extra_cooling_c: float = 0.0,
    drift_c: float = 0.0,
    seed: int = 0,
    plant_patch: bool = True,
) -> xr.Dataset:
    """A tile that greens over `PATCH` in the last window.

    The law `lst = 45 + NDVI_TO_LST * ndvi` holds in every window including the last, so
    with `extra_cooling_c == 0` a model that learned the law before the change predicts
    the patch exactly. `extra_cooling_c` adds cooling the features cannot explain, which
    is what a genuinely mistracked change looks like.

    `drift_c` shifts every cell in the final window, standing in for a hotter year - the
    whole-window offset the difference-in-differences is supposed to cancel.
    """
    rng = np.random.default_rng(seed)
    n, (height, width) = len(WINDOWS), SHAPE

    # Spatial variation present in every window, so the slope is learnable from `before`.
    # The range is deliberately wide enough that the greened patch lands *inside* it:
    # gradient boosting predicts a constant beyond its last split, so a patch greener
    # than anything in training tests extrapolation rather than tracking. That failure
    # gets its own test.
    base = rng.uniform(0.05, 0.85, (height, width))
    base[PATCH] = NDVI_PATCH_BEFORE
    ndvi = np.repeat(base[None, :, :], n, axis=0) + rng.normal(0, 0.01, (n, height, width))
    if plant_patch:
        ndvi[-1][PATCH] = NDVI_PATCH_BEFORE + NDVI_GREENING

    lst = 45.0 + NDVI_TO_LST * ndvi + rng.normal(0, 0.05, ndvi.shape)
    lst[-1][PATCH] += extra_cooling_c
    lst[-1] += drift_c

    static = rng.normal(0, 0.01, (height, width))
    return xr.Dataset(
        {
            "ndvi": (("time", "y", "x"), ndvi),
            "ndbi": (("time", "y", "x"), -ndvi * 0.5 + rng.normal(0, 0.01, ndvi.shape)),
            "albedo": (("time", "y", "x"), np.full((n, height, width), 0.18) + static),
            "lst_c": (("time", "y", "x"), lst),
            "elevation_m": (("y", "x"), 215.0 + static),
            "landcover": (("y", "x"), np.full((height, width), 50, dtype="uint8")),
            "air_temp_c": (("time",), np.linspace(33.0, 34.0, n)),
            "wind_speed_ms": (("time",), np.linspace(2.0, 2.2, n)),
            "relative_humidity_pct": (("time",), np.linspace(40.0, 42.0, n)),
        },
        coords={
            "y": np.arange(height, dtype="float64") * 100.0,
            "x": np.arange(width, dtype="float64") * 100.0,
            "time": np.arange(n),
            "window": ("time", np.array(WINDOWS, dtype="<U32")),
            "season": ("time", np.array(["summer"] * n, dtype="<U16")),
        },
    )


# ------------------------------------------------------------- change detection ---


def test_a_planted_patch_is_found_and_the_rest_of_the_tile_is_not() -> None:
    field = detect_change(_cube(), WINDOWS[:-1], WINDOWS[-1:])

    assert field.changed[PATCH].all(), "the greened patch must be detected"

    # A robust 3-sigma cutoff flags a fraction of a percent of an unchanged tile by
    # construction. Those specks are noise, and the site filter is what removes them -
    # so the contract worth asserting is that no *site* appears outside the patch.
    stray = field.changed.sum() - field.changed[PATCH].sum()
    assert stray < 0.01 * field.changed.size, f"{stray} stray cells is more than noise"
    assert field.site_mask[PATCH].all()
    assert field.site_mask.sum() == field.site_mask[PATCH].sum(), "a site outside the patch"


def test_a_tile_that_only_drifts_reports_no_change() -> None:
    """Every cell greening together is a wet year, not an intervention.

    This is the failure a hardcoded threshold produces: shift the whole tile by 0.15 and
    a fixed 0.1 cutoff reports all 1,600 cells as a change site.
    """
    # No planted patch: this tile's only story is that every cell greened together.
    cube = _cube(plant_patch=False)
    cube["ndvi"].values[-1] += 0.15

    field = detect_change(cube, WINDOWS[:-1], WINDOWS[-1:])

    # Individual cells still cross a 3-sigma cutoff; none of them form a site, which is
    # the level at which "did anything actually happen here" is decided.
    assert field.sites == []
    assert field.n_in_sites == 0
    assert field.median_drift == pytest.approx(0.15, abs=0.02)


def test_sites_are_ranked_and_tiny_specks_are_dropped() -> None:
    cube = _cube()
    # A speck too small to resolve as a site at 100 m.
    cube["ndvi"].values[-1][0, 0] = 0.9

    field = detect_change(cube, WINDOWS[:-1], WINDOWS[-1:])

    assert field.changed[0, 0], "the speck is still a changed cell"
    assert [s.n_cells for s in field.sites] == sorted(
        [s.n_cells for s in field.sites], reverse=True
    )
    assert all(s.n_cells >= MIN_SITE_CELLS for s in field.sites)
    assert len(field.sites) == 1, "only the planted patch is large enough to be a site"
    assert field.sites[0].greening


def test_browning_and_greening_are_separate_sites() -> None:
    """Averaging a new park with a new car park would report no change at all."""
    cube = _cube()
    cube["ndvi"].values[-1][25:35, 25:35] = -0.2

    field = detect_change(cube, WINDOWS[:-1], WINDOWS[-1:])

    assert {s.greening for s in field.sites} == {True, False}


def test_a_change_needs_windows_on_both_sides() -> None:
    with pytest.raises(ValueError, match="each side"):
        detect_change(_cube(), [], WINDOWS[-1:])


# -------------------------------------------------------------------- hindcast ---


def test_training_on_the_window_being_predicted_is_refused() -> None:
    """The one mistake that would invalidate every number downstream."""
    cube = _cube()
    field = detect_change(cube, WINDOWS[:-1], WINDOWS[-1:])

    with pytest.raises(ValueError, match="trains on the window it predicts"):
        run_hindcast(cube, WINDOWS, WINDOWS[-1], field)


def test_a_model_that_tracks_the_change_scores_near_zero_effect_error() -> None:
    """The headline estimator, on a tile whose response the model can actually learn.

    LST is a linear function of NDVI everywhere, so a model trained before the change
    already knows what greenness is worth and should carry that to the greened patch.
    """
    cube = _cube()
    field = detect_change(cube, WINDOWS[:-1], WINDOWS[-1:])

    report = run_hindcast(cube, WINDOWS[:-1], WINDOWS[-1], field, num_boost_round=200)

    assert report.observed_change_effect < -1.0, "the planted cooling must be visible"
    assert abs(report.change_effect_error) < 1.0, (
        f"model mistracked the change by {report.change_effect_error:.2f} degC"
    )


def test_a_whole_window_offset_cancels_out_of_the_effect_error() -> None:
    """Why the estimator is a difference of biases rather than an MAE.

    A hotter year than any in training shifts every prediction together. MAE degrades
    badly; the change-effect error must not, because both groups moved by the same amount.
    """
    field = detect_change(_cube(), WINDOWS[:-1], WINDOWS[-1:])
    plain = run_hindcast(_cube(), WINDOWS[:-1], WINDOWS[-1], field, num_boost_round=200)

    shifted_cube = _cube(drift_c=5.0)
    shifted_field = detect_change(shifted_cube, WINDOWS[:-1], WINDOWS[-1:])
    shifted = run_hindcast(
        shifted_cube, WINDOWS[:-1], WINDOWS[-1], shifted_field, num_boost_round=200
    )

    assert shifted.overall.mae > plain.overall.mae + 2.0, "the offset must hurt MAE"
    assert abs(shifted.change_effect_error - plain.change_effect_error) < 0.5, (
        "the offset must cancel out of the change-effect error"
    )


def test_a_model_blind_to_the_change_is_caught() -> None:
    """The test has to be able to fail.

    Here the patch cools far more than its greenness explains - a change the features
    cannot account for. The model should under-predict the cooling, and the estimator
    should say so rather than reporting a comfortable number.
    """
    cube = _cube(extra_cooling_c=-8.0)
    field = detect_change(cube, WINDOWS[:-1], WINDOWS[-1:])

    report = run_hindcast(cube, WINDOWS[:-1], WINDOWS[-1], field, num_boost_round=200)

    assert report.change_effect_error > 2.0, (
        "a cooling the features cannot explain must show as a large positive error"
    )


def test_greening_beyond_the_training_range_under_predicts_and_is_visible() -> None:
    """Gradient boosting cannot extrapolate, and the hindcast has to expose that.

    A tree predicts a constant beyond its last split, so a patch greener than anything
    the model was trained on gets the cooling of the greenest cell it ever saw and no
    more. This is the plan's "extrapolation in the built-up core" risk made concrete, and
    it is the most likely way a real hindcast under-states a big intervention.
    """
    cube = _cube()
    # Far greener than the tile-wide maximum of ~0.85.
    cube["ndvi"].values[-1][PATCH] = 1.6
    cube["lst_c"].values[-1][PATCH] = 45.0 + NDVI_TO_LST * 1.6

    field = detect_change(cube, WINDOWS[:-1], WINDOWS[-1:])
    report = run_hindcast(cube, WINDOWS[:-1], WINDOWS[-1], field, num_boost_round=200)

    assert report.change_effect_error > 1.0, (
        "under-prediction from extrapolation must show up, not average away"
    )


# ------------------------------------------------------- matched control group ---


def test_matching_removes_a_confound_the_raw_estimator_falls_for() -> None:
    """The reason the matched estimator exists, as a case where the raw one is wrong.

    Land that greens is land that had room to green, so changed cells sit at the low end
    of baseline NDVI. Here the *whole* low-NDVI stratum is warmer than the model expects,
    change or no change - a covariate effect, not a response to the change. The raw
    difference of biases attributes all of it to the change; matching on baseline NDVI
    does not, because it compares low-NDVI changed cells against low-NDVI controls.
    """
    cube = _cube()
    # A band of the tile that is permanently low-NDVI and permanently runs hot relative
    # to the NDVI law. The greened patch sits inside it; most of the tile does not.
    confounded = (slice(5, 25), slice(5, 25))
    cube["ndvi"].values[:, 5:25, 5:25] = NDVI_PATCH_BEFORE
    cube["ndvi"].values[-1][PATCH] = NDVI_PATCH_BEFORE + NDVI_GREENING
    cube["lst_c"].values[:] = 45.0 + NDVI_TO_LST * cube["ndvi"].values
    cube["lst_c"].values[-1][confounded] += 3.0  # the confound, in the after window

    field = detect_change(cube, WINDOWS[:-1], WINDOWS[-1:])
    report = run_hindcast(cube, WINDOWS[:-1], WINDOWS[-1], field, num_boost_round=200)

    raw = report.change_effect_error
    matched = report.matched_effect_error.value

    assert report.matched_effect_error.matched_cells > 0, "no stratum had a control"
    assert abs(matched) < abs(raw) / 2, (
        f"matching should absorb the covariate gap: raw {raw:+.2f}, matched {matched:+.2f}"
    )


def test_matching_leaves_a_genuine_effect_alone() -> None:
    """Matching must not eat the signal it is meant to isolate.

    A guard against over-correcting: with no confound, the matched and raw estimators
    should agree, or the refinement would be removing real effect along with bias.
    """
    cube = _cube(extra_cooling_c=-8.0)
    field = detect_change(cube, WINDOWS[:-1], WINDOWS[-1:])
    report = run_hindcast(cube, WINDOWS[:-1], WINDOWS[-1], field, num_boost_round=200)

    assert report.matched_effect_error.value == pytest.approx(
        report.change_effect_error, abs=1.0
    )


def test_a_stratum_with_no_control_is_counted_not_silently_dropped() -> None:
    """Unmatched cells are a reason to distrust the number, so they have to be visible."""
    cube = _cube()
    # Give the greened patch a land-cover class that exists nowhere else, so its strata
    # can never find a control.
    cube["landcover"].values[PATCH] = 10

    field = detect_change(cube, WINDOWS[:-1], WINDOWS[-1:])
    report = run_hindcast(cube, WINDOWS[:-1], WINDOWS[-1], field, num_boost_round=50)

    assert report.matched_effect_error.unmatched_cells > 0
    assert report.matched_effect_error.matched_cells == 0
    assert np.isnan(report.matched_effect_error.value), "no support means no answer"
