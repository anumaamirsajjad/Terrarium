"""Air core tests. No network, no files - the core is pure, so this is arrays in, arrays out."""

from __future__ import annotations

import warnings

import numpy as np
import pytest
import xarray as xr

from terrarium.cores.air import (
    EMISSION_VARIABLE,
    SEASONAL_FETCH_M,
    AirParameters,
    concentration,
    leave_one_station_out,
    plume_kernel,
    seasonal_kernel,
    simulate,
)
from terrarium.cores.base import Intervention
from terrarium.cores.thermal.simulate import BUILT_UP_CLASS, TREE_COVER_CLASS, WATER_CLASS

RES = 100.0
SUMMER = AirParameters.for_season("summer")

# Wind blowing from the west, i.e. towards the east. Meteorological convention.
WESTERLY = 270.0


def _cube(
    *,
    emissions: np.ndarray,
    season: str = "summer",
    wind_speed: float = 2.0,
    wind_direction: float = WESTERLY,
    ndvi: float = 0.2,
) -> xr.Dataset:
    """One window's slice, shaped like the real thing.

    The bottom row is tree cover at NDVI 0.6, which is what the canopy proxy calibrates
    against: without a greener reference somewhere on the tile, every cell reads as already
    fully canopied and no planting has anywhere to go.
    """
    height, width = emissions.shape
    greenness = np.full((height, width), ndvi, dtype="float32")
    greenness[-1, :] = 0.6
    landcover = np.full((height, width), BUILT_UP_CLASS, dtype="uint8")
    landcover[-1, :] = TREE_COVER_CLASS

    return xr.Dataset(
        {
            EMISSION_VARIABLE: (("y", "x"), emissions.astype("float32")),
            "ndvi": (("y", "x"), greenness),
            "landcover": (("y", "x"), landcover),
            "wind_speed_ms": ((), np.float32(wind_speed)),
            "wind_direction_deg": ((), np.float32(wind_direction)),
        },
        coords={
            "y": np.arange(height, dtype="float64")[::-1] * RES,
            "x": np.arange(width, dtype="float64") * RES,
            "season": season,
        },
    )


def _point_source(shape: tuple[int, int] = (81, 81), strength: float = 1.0) -> np.ndarray:
    field = np.zeros(shape, dtype="float64")
    field[shape[0] // 2, shape[1] // 2] = strength
    return field


# --------------------------------------------------------------------------------
# The kernel
# --------------------------------------------------------------------------------


def test_plume_goes_downwind_not_upwind() -> None:
    """A westerly blows east. Getting this backwards draws a plausible, wrong map."""
    kernel = plume_kernel(
        SUMMER,
        wind_speed_ms=2.0,
        wind_direction_deg=WESTERLY,
        deposition_velocity_m_s=0.0,
        resolution_m=RES,
    )
    centre = SUMMER.kernel_radius_cells

    assert kernel[centre, centre + 5] > 0, "nothing landed east of the source"
    assert kernel[centre, centre - 5] == 0, "material travelled upwind"
    # And nothing directly north or south either: that is crosswind, not downwind.
    assert kernel[centre + 5, centre] == 0
    assert kernel[centre - 5, centre] == 0


@pytest.mark.parametrize(
    ("direction", "row", "col"),
    [
        (270.0, 0, +1),  # from the west  -> east
        (90.0, 0, -1),  # from the east  -> west
        (180.0, -1, 0),  # from the south -> north, which is a *lower* row index
        (0.0, +1, 0),  # from the north -> south
    ],
)
def test_every_cardinal_direction_lands_on_the_right_side(
    direction: float, row: int, col: int
) -> None:
    kernel = plume_kernel(
        SUMMER,
        wind_speed_ms=2.0,
        wind_direction_deg=direction,
        deposition_velocity_m_s=0.0,
        resolution_m=RES,
    )
    centre = SUMMER.kernel_radius_cells
    assert kernel[centre + 5 * row, centre + 5 * col] > 0
    assert kernel[centre - 5 * row, centre - 5 * col] == 0


def test_crosswind_integral_conserves_mass() -> None:
    """The one physical invariant worth asserting.

    With no deposition, every gram released must still cross every downwind plane. For a
    plume mixed through depth H moving at speed u, that means the crosswind integral of
    concentration is exactly Q / (u H), at every distance, forever.
    """
    speed = 2.0
    kernel = plume_kernel(
        SUMMER,
        wind_speed_ms=speed,
        wind_direction_deg=WESTERLY,
        deposition_velocity_m_s=0.0,
        resolution_m=RES,
    )
    centre = SUMMER.kernel_radius_cells
    expected = 1.0 / (speed * SUMMER.mixing_height_m)

    for downwind in (3, 10, 25):
        flux = kernel[:, centre + downwind].sum() * RES
        assert flux == pytest.approx(expected, rel=0.02), f"{downwind} cells downwind"


def test_deposition_removes_mass_with_distance() -> None:
    clean = plume_kernel(
        SUMMER,
        wind_speed_ms=2.0,
        wind_direction_deg=WESTERLY,
        deposition_velocity_m_s=0.0,
        resolution_m=RES,
    )
    depositing = plume_kernel(
        SUMMER,
        wind_speed_ms=2.0,
        wind_direction_deg=WESTERLY,
        deposition_velocity_m_s=0.02,
        resolution_m=RES,
    )
    centre = SUMMER.kernel_radius_cells
    near = depositing[:, centre + 3].sum() / clean[:, centre + 3].sum()
    far = depositing[:, centre + 25].sum() / clean[:, centre + 25].sum()

    assert far < near < 1.0, "loss must accumulate along the trajectory, not jump"


# --------------------------------------------------------------------------------
# The field
# --------------------------------------------------------------------------------


def test_winter_inversion_concentrates_the_same_emissions() -> None:
    """The whole reason winter is in the cube. Same sources, worse air.

    A shallower mixing height and slower lateral spread should raise peak concentration
    several-fold - not by a few percent, which would mean the season is decorative.
    """
    emissions = _point_source()
    winter = AirParameters.for_season("winter")

    canopy = np.zeros_like(emissions)
    args: dict[str, float] = {
        "wind_speed_ms": 1.5,
        "wind_direction_deg": WESTERLY,
        "resolution_m": RES,
    }
    summer_field = concentration(emissions, canopy, SUMMER, seasonal=True, **args)
    winter_field = concentration(emissions, canopy, winter, seasonal=True, **args)

    assert winter_field.max() > 3 * summer_field.max()


def test_calm_air_does_not_produce_an_infinity() -> None:
    """Concentration goes as 1/u, and ERA5 reports near-zero winds in winter."""
    field = concentration(
        _point_source(),
        np.zeros((81, 81)),
        SUMMER,
        wind_speed_ms=0.0,
        wind_direction_deg=WESTERLY,
        resolution_m=RES,
    )
    assert np.isfinite(field).all()
    assert field.max() > 0


def test_no_emissions_no_concentration() -> None:
    field = concentration(
        np.zeros((41, 41)),
        np.zeros((41, 41)),
        SUMMER,
        wind_speed_ms=2.0,
        wind_direction_deg=WESTERLY,
        resolution_m=RES,
    )
    assert not field.any()


# --------------------------------------------------------------------------------
# The intervention
# --------------------------------------------------------------------------------


def test_removing_emissions_cleans_the_air_around_the_zone() -> None:
    """`simulate` defaults to the **seasonal** kernel, which spreads in every direction.

    That is a deliberate change of contract, and it is the one the monitors support: over a
    season Lahore's overpass-hour wind covers nearly the whole compass, so a low-emission
    zone benefits its neighbours all round rather than only the streets behind it. The
    single-direction behaviour is still asserted, on `plume_kernel` and on the test below.
    """
    emissions = np.zeros((81, 81))
    emissions[30:50, 30:40] = 0.01  # a busy corridor
    cube = _cube(emissions=emissions)

    mask = np.zeros((81, 81), dtype=bool)
    mask[30:50, 30:40] = True
    result = simulate(cube, Intervention(mask=mask, canopy_fraction_added=0.0,
                                         emission_fraction_removed=1.0))

    assert result.variable == "pm25_ugm3"
    # Negative is cleaner, everywhere, and strongest inside.
    assert result.delta.max() <= 0
    assert result.stats.mean_delta_inside < 0
    # Neighbours on *both* sides improve, which is what an isotropic seasonal mean means.
    assert result.delta[40, 60] < 0, "east of the corridor"
    assert result.delta[40, 20] < 0, "west of the corridor"
    # And it still decays with distance rather than being flat.
    assert result.delta[40, 60] > result.delta[40, 45]


def test_the_plume_mode_still_only_travels_downwind() -> None:
    """The hourly, directional model is intact and reachable; it is just not the default.

    Kept because `plume_kernel` remains correct physics for a single hour, and because the
    seasonal kernel is normalised against it — if the plume's total drifted, magnitudes
    would move without anything else noticing.
    """
    emissions = np.zeros((81, 81))
    emissions[30:50, 30:40] = 0.01
    canopy = np.zeros((81, 81))
    args = {"wind_speed_ms": 2.0, "wind_direction_deg": WESTERLY, "resolution_m": RES}

    field = concentration(emissions, canopy, SUMMER, seasonal=False, **args)

    assert field[40, 60] > 0, "downwind of the corridor"
    # Not exactly zero: an FFT convolution leaves round-off ~1e-17 against a peak of
    # ~1e-1. Anything above this floor would be transport, not arithmetic.
    assert abs(field[40, 5]) < 1e-12, "nothing travels upwind"


def test_the_seasonal_kernel_integrates_to_fetch_over_ventilation() -> None:
    """The magnitude is stated in closed form, not copied from the plume.

    A plume's crosswind integral is 1/(u*H) at every downwind step, so over a fetch L its
    total is L/(u*H). Asserting that directly is what decouples this kernel from
    `kernel_radius_cells`, which is a *fetch* limit and means nothing here.
    """
    speed, height = 1.5, SUMMER.mixing_height_m
    kernel = seasonal_kernel(
        SUMMER, wind_speed_ms=speed, deposition_velocity_m_s=0.0, resolution_m=RES
    )
    expected = SEASONAL_FETCH_M / (speed * height * RES * RES)

    assert kernel.sum() == pytest.approx(expected, rel=1e-6)


def test_the_seasonal_kernel_is_isotropic() -> None:
    """Opposite offsets carry equal weight, which the plume never does."""
    kernel = seasonal_kernel(
        SUMMER, wind_speed_ms=1.5, deposition_velocity_m_s=0.002, resolution_m=RES
    )
    centre = kernel.shape[0] // 2

    assert kernel[centre, centre + 12] == pytest.approx(kernel[centre, centre - 12])
    assert kernel[centre + 12, centre] == pytest.approx(kernel[centre, centre + 12])


def test_the_plume_s_fetch_limit_does_not_move_seasonal_magnitudes() -> None:
    """The coupling this normalisation exists to remove.

    The first version normalised to `plume_kernel(...).sum()`, and a plume's total scales
    with `kernel_radius_cells`. So changing a *plume* parameter moved every seasonal
    magnitude by 68 %, while altering this kernel's own shape not at all.
    """
    args = {"wind_speed_ms": 0.64, "deposition_velocity_m_s": 0.001, "resolution_m": RES}
    wide = seasonal_kernel(SUMMER.model_copy(update={"kernel_radius_cells": 200}), **args)
    narrow = seasonal_kernel(SUMMER.model_copy(update={"kernel_radius_cells": 120}), **args)

    assert wide.sum() == pytest.approx(narrow.sum(), rel=1e-9)


def test_deposition_removes_mass_from_the_seasonal_total() -> None:
    """Normalised against the *undeposited* sum, so deposition is a loss, not a rescale."""
    args = {"wind_speed_ms": 1.0, "resolution_m": RES}
    clean = seasonal_kernel(SUMMER, deposition_velocity_m_s=0.0, **args)
    depositing = seasonal_kernel(SUMMER, deposition_velocity_m_s=0.02, **args)

    assert depositing.sum() < clean.sum()


def test_the_seasonal_kernel_still_concentrates_under_a_winter_inversion() -> None:
    """The seasonal factor is the core's headline finding and must survive the change.

    Measured on the real cubes it is 6.3x-8.9x, varying by year because it tracks each
    window's wind speed as well as its mixing height. Asserted loosely here because the
    kernel alone, at one fixed wind speed, only carries the mixing-height half.
    """
    args = {"wind_speed_ms": 1.5, "deposition_velocity_m_s": 0.001, "resolution_m": RES}
    winter = seasonal_kernel(AirParameters.for_season("winter"), **args)
    summer = seasonal_kernel(AirParameters.for_season("summer"), **args)

    assert winter.max() > 3 * summer.max()


def test_doing_nothing_changes_nothing() -> None:
    """The zero-intervention case, which a linear model gets right by construction and a
    buggy one gets wrong by float noise."""
    cube = _cube(emissions=_point_source(strength=0.05))
    mask = np.zeros((81, 81), dtype=bool)
    mask[40, 40] = True

    result = simulate(cube, Intervention(mask=mask, canopy_fraction_added=0.0))

    assert np.abs(result.delta).max() == 0.0


def test_planting_trees_cleans_the_air_a_little() -> None:
    """Canopy raises dry deposition. Real, second-order, and must have the right sign."""
    emissions = np.zeros((81, 81))
    emissions[35:45, 35:45] = 0.02
    cube = _cube(emissions=emissions, ndvi=0.15)

    mask = np.ones((81, 81), dtype=bool)
    result = simulate(cube, Intervention(mask=mask, canopy_fraction_added=0.5))

    assert result.stats.mean_delta_inside < 0
    # Second-order: a whole-tile planting must not out-perform removing the emissions.
    removal = simulate(
        cube, Intervention(mask=mask, canopy_fraction_added=0.0, emission_fraction_removed=1.0)
    )
    assert removal.stats.mean_delta_inside < result.stats.mean_delta_inside


def test_water_is_not_plantable_here_either() -> None:
    """The air core reuses the thermal core's capping, so the river stays a river."""
    emissions = np.zeros((41, 41))
    emissions[20, 20] = 0.01
    cube = _cube(emissions=emissions)
    dry = simulate(cube, Intervention(mask=np.ones((41, 41), dtype=bool),
                                      canopy_fraction_added=1.0))

    # Same tile, now a lake everywhere except the tree row the proxy calibrates against.
    cube["landcover"].values[:-1, :] = WATER_CLASS
    flooded = simulate(cube, Intervention(mask=np.ones((41, 41), dtype=bool),
                                          canopy_fraction_added=1.0))

    assert dry.stats.mean_delta_inside < 0
    assert flooded.stats.mean_delta_inside == 0.0, "planted on open water"


def test_season_is_read_from_the_cube_not_assumed() -> None:
    emissions = _point_source(strength=0.05)
    summer = simulate(
        _cube(emissions=emissions, season="summer"),
        Intervention(mask=np.ones((81, 81), dtype=bool), canopy_fraction_added=0.0,
                     emission_fraction_removed=1.0),
    )
    winter = simulate(
        _cube(emissions=emissions, season="winter"),
        Intervention(mask=np.ones((81, 81), dtype=bool), canopy_fraction_added=0.0,
                     emission_fraction_removed=1.0),
    )
    assert abs(winter.stats.min_delta) > 3 * abs(summer.stats.min_delta)


def test_an_unpopulated_inventory_is_refused() -> None:
    """A cube that opens is not a cube that is complete."""
    cube = _cube(emissions=np.full((41, 41), np.nan))
    with pytest.raises(ValueError, match="unpopulated"):
        simulate(cube, Intervention(mask=np.ones((41, 41), dtype=bool), canopy_fraction_added=0.0))


def test_unknown_season_has_no_parameters() -> None:
    with pytest.raises(ValueError, match="monsoon"):
        AirParameters.for_season("monsoon")


# --------------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------------


def test_leave_one_out_recovers_a_known_scale_and_background() -> None:
    modelled = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    observed = 3.0 * modelled + 40.0

    report = leave_one_station_out(modelled, observed, [f"s{i}" for i in range(5)])

    assert report.scale == pytest.approx(3.0)
    assert report.background_ugm3 == pytest.approx(40.0)
    assert report.mae == pytest.approx(0.0, abs=1e-9)
    assert report.beats_null


def test_a_model_with_no_signal_does_not_beat_the_null() -> None:
    """The check that stops a fit on five points claiming skill it does not have.

    `modelled` here is unrelated to `observed`, so a straight line through four of them
    extrapolates to the fifth worse than simply guessing the others' mean. Without the
    null comparison this report would show an unremarkable MAE and read as validation.
    """
    modelled = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    observed = np.array([100.0, 20.0, 90.0, 30.0, 95.0])

    report = leave_one_station_out(modelled, observed, [f"s{i}" for i in range(5)])

    assert not report.beats_null


def test_stations_that_all_model_the_same_are_refused() -> None:
    """No slope to fit, and numpy will happily invent one."""
    with pytest.raises(ValueError, match="no slope"):
        leave_one_station_out(
            np.ones(5), np.array([30.0, 90.0, 50.0, 120.0, 60.0]), [f"s{i}" for i in range(5)]
        )


def test_too_few_stations_is_refused_rather_than_fitted() -> None:
    with pytest.raises(ValueError, match="at least 4"):
        leave_one_station_out(np.array([1.0, 2.0, 3.0]), np.array([4.0, 5.0, 6.0]), list("abc"))


def test_a_fold_with_no_spread_is_refused_even_when_the_whole_set_has_some() -> None:
    """The degenerate case the whole-set check cannot see.

    Three monitors on the same modelled concentration plus one in a plume: `ptp` over all
    four is 4.0, so a single up-front check passes — and then holding out the only station
    with a different x leaves three identical ones, which numpy fits with nothing but a
    `RankWarning` and reports as a confident scale factor.

    With a handful of monitors in one city this is an ordinary arrangement, and it is
    exactly what the guard exists to refuse, so it has to run per fold.
    """
    modelled = np.array([1.0, 1.0, 1.0, 5.0])
    observed = np.array([10.0, 12.0, 11.0, 40.0])

    assert np.ptp(modelled) > 0, "the whole-set check must pass, or this tests nothing"
    with pytest.raises(ValueError, match="holding out d"):
        leave_one_station_out(modelled, observed, list("abcd"))


def test_a_well_spread_set_still_fits_without_warnings() -> None:
    """The per-fold guard must not refuse the case it is meant to allow."""
    modelled = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    observed = 3.0 * modelled + 12.0

    with warnings.catch_warnings():
        warnings.simplefilter("error")  # a RankWarning here is a failure, not a note
        report = leave_one_station_out(modelled, observed, [f"s{i}" for i in range(5)])

    assert report.scale == pytest.approx(3.0)
    assert report.background_ugm3 == pytest.approx(12.0)


def test_a_window_with_no_wind_is_refused_not_answered_with_zero() -> None:
    """The worst failure this core can have: a confident zero.

    NaN meteorology propagates into the bearing, every comparison against NaN is False,
    and the kernel comes out entirely zero - so a plan that removes every vehicle reports
    exactly no effect. Zero is also a legitimate answer, which is what makes this
    indistinguishable from a finding unless it raises.
    """
    emissions = _point_source(shape=(41, 41), strength=0.05)
    mask = np.ones((41, 41), dtype=bool)

    for field in ("wind_direction_deg", "wind_speed_ms"):
        cube = _cube(emissions=emissions)
        cube[field].values[...] = np.nan
        with pytest.raises(ValueError, match="no direction or speed"):
            simulate(
                cube,
                Intervention(
                    mask=mask, canopy_fraction_added=0.0, emission_fraction_removed=1.0
                ),
            )


def test_a_north_up_cube_is_refused() -> None:
    """A y-ascending cube would mirror every plume north-south, smoothly and wrongly."""
    cube = _cube(emissions=_point_source(shape=(41, 41), strength=0.05))
    flipped = cube.assign_coords(y=cube["y"].values[::-1])

    with pytest.raises(ValueError, match="ascend"):
        simulate(
            flipped,
            Intervention(mask=np.ones((41, 41), dtype=bool), canopy_fraction_added=0.0,
                         emission_fraction_removed=1.0),
        )
