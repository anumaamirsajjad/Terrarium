"""Where the smoke goes: a steady-state Gaussian plume core on the 100 m grid.

The second core (D8), and the one that answers "ban combustion vehicles inside this ring".
Pure, like every core: the cube, the intervention and the parameter set arrive as
arguments and nothing here reads a file, a clock, or config.

### What this computes, and what it does not

**A local increment, not the concentration a monitor reads.** Lahore's PM2.5 is dominated
by a regional background - crop residue burning, transboundary transport, everything
outside a 20 km tile - and none of that is in the inventory. What the core produces is the
contribution of *this tile's own road traffic and kilns*, which is typically a fraction of
the total. Quoted as an absolute it would be wrong by a large factor. Quoted as a **delta**
it is exactly the right object, because the background is identical on both sides and
cancels, the same way meteorology cancels in the thermal core.

That is also why `scripts/validate_air.py` fits an intercept: the intercept is the
background, and the slope is the scale error in the emission inventory.

### The physics, and where it stops

Every cell is a ground-level source. Downwind of it the plume spreads laterally as a
Gaussian and is assumed **well mixed vertically** up to the mixing height - the standard
approximation once a plume is older than a few minutes, and the reason the mixing height
appears directly in the denominator. Concentration is the superposition of every cell's
plume, which because the wind is uniform across a 20 km tile is a **convolution**: one
kernel, one FFT, the whole tile at once. That is what keeps this interactive.

Winter is not a scale factor on summer. Lahore's cold-season inversion drops the mixing
height by roughly 3x and the stable air spreads the plume more slowly, so the same
emissions produce a much higher, much narrower concentration field. Both effects live in
`AirParameters.for_season`, which is why the season is read from the cube rather than
assumed.

Deliberately absent: buildings (street-canyon trapping is a sub-100 m effect), chemistry
(secondary aerosol forms over hours, downwind of the tile), and any diurnal cycle - this
is the ~10:30 overpass hour, matching the thermal core, so the two are describing the same
moment.
"""

from __future__ import annotations

import numpy as np
import xarray as xr
from pydantic import BaseModel, ConfigDict, Field
from scipy.signal import fftconvolve

from terrarium.cores.base import CoreResult, Intervention, summarise_delta
from terrarium.cores.thermal.simulate import canopy_fraction, effective_fraction

EMISSION_VARIABLE = "pm25_emission_g_s"
RESULT_VARIABLE = "pm25_ugm3"
RESULT_UNITS = "ug m-3"

G_PER_M3_TO_UG_PER_M3 = 1e6

# Air spillover is not thermal spillover. Cooling stops at the edge of the 500 m feature
# neighbourhood; a plume is still measurable kilometres downwind, and reporting its
# spillover over a 200 m ring would measure the wrong thing entirely. 10 cells = 1 km,
# which is where the modelled delta has decayed to a few percent of its peak.
AIR_SPILLOVER_CELLS = 10


class AirParameters(BaseModel):
    """The dispersion constants. The air core's equivalent of the thermal core's booster.

    It occupies the same argument slot for the same reason: a core takes its model as an
    argument. The difference is worth stating plainly - **these are literature values, not
    fitted ones**. The thermal core learned its response from this tile's own Landsat
    record; this one is handed Pasquill-Gifford coefficients and a fleet emission factor,
    and the only thing that has ever seen Lahore's air is the OpenAQ calibration in
    `scripts/validate_air.py`.
    """

    model_config = ConfigDict(frozen=True)

    # Depth of the layer the plume is assumed well mixed through. The single most
    # important number here: concentration is inversely proportional to it, so the winter
    # inversion is most of why winter air is worse for identical emissions.
    mixing_height_m: float = Field(gt=0.0)
    # Lateral spread: sigma_y = initial + coefficient * downwind distance. Pasquill-Gifford
    # class C (slightly unstable, mid-morning sun) is ~0.11; class E/F (stable, under an
    # inversion) is ~0.05. Linear rather than the published power law, which is within a
    # few percent over the 0.1-5 km range a 20 km tile actually spans.
    sigma_y_coefficient: float = Field(gt=0.0)
    # Spread a source already has at zero distance. Half a cell: a 100 m cell is an area
    # source, not a point, and without this the self-contribution diverges.
    sigma_y_initial_m: float = 50.0
    # Dry deposition velocity for PM2.5 over bare/built ground and over closed canopy.
    # Foliage is a far better sink than concrete - more surface area, and it is sticky.
    deposition_velocity_bare_m_s: float = 0.001
    deposition_velocity_canopy_m_s: float = 0.010
    # Calm air is where a Gaussian plume model breaks: concentration goes as 1/u, so a
    # reanalysis value near zero produces an infinity. Below this, treat it as this.
    min_wind_speed_ms: float = 0.5
    # How far downwind the kernel reaches, in cells. **This is a fetch limit, not a
    # numerical tolerance**, and it was worth measuring rather than guessing: over an area
    # source, concentration accumulates roughly linearly with the distance of upwind
    # sources still contributing, so a radius of 30 cells (3 km) returned 1.41 ug/m3 where
    # 200 returns 3.84 - a 63 % truncation that looks like a modelling result.
    #
    # 200 cells is 20 km, which is the tile. Everything upwind of that is outside what the
    # inventory knows about and belongs to the regional background this core does not
    # model - the same limitation as the missing background, stated in distance. It costs
    # ~40 ms per convolution against a < 3 s interactive budget.
    kernel_radius_cells: int = 200

    @classmethod
    def for_season(cls, season: str) -> AirParameters:
        """Parameters for a summer or a winter window. Winter is the inversion case.

        Raises on any other season rather than defaulting: silently handing summer
        parameters to a winter window would understate concentrations ~3x, which is the
        entire finding this core exists to produce.
        """
        if season == "winter":
            # Nov-Jan: nocturnal inversion still capping the boundary layer at 10:30,
            # stable, poorly ventilated. This is Lahore's smog season.
            return cls(mixing_height_m=250.0, sigma_y_coefficient=0.05)
        if season == "summer":
            # Apr-Jun: strong insolation has already broken the inversion by mid-morning.
            return cls(mixing_height_m=800.0, sigma_y_coefficient=0.11)
        raise ValueError(f"no dispersion parameters for season {season!r}")


def plume_kernel(
    params: AirParameters,
    *,
    wind_speed_ms: float,
    wind_direction_deg: float,
    deposition_velocity_m_s: float,
    resolution_m: float,
) -> np.ndarray:
    """Concentration at each grid offset from a 1 g/s source, in s m-3.

    Indexed by *receptor minus source* offset with the source at the centre, which is
    exactly what `fftconvolve` wants: one convolution then superposes every cell's plume.

    `wind_direction_deg` is meteorological - the direction the wind comes **from** - so a
    270 deg westerly sends the plume east. Getting this backwards produces a beautiful,
    entirely wrong map, and nothing downstream would flag it.
    """
    speed = max(wind_speed_ms, params.min_wind_speed_ms)
    radius = params.kernel_radius_cells

    # Unit vector pointing the way the air travels, in (east, north).
    bearing = np.radians(wind_direction_deg)
    downwind = np.array([-np.sin(bearing), -np.cos(bearing)])
    crosswind = np.array([-downwind[1], downwind[0]])

    offsets = np.arange(-radius, radius + 1) * resolution_m
    # Row index increases southwards, so north offset is the negated row offset.
    north = -offsets[:, None] * np.ones_like(offsets)[None, :]
    east = np.ones_like(offsets)[:, None] * offsets[None, :]

    along = east * downwind[0] + north * downwind[1]
    across = np.abs(east * crosswind[0] + north * crosswind[1])
    # A cell's own emissions are not released at its centre and measured there; they are
    # released across it. Half a cell downwind is the mean travel distance within it.
    along[radius, radius] = resolution_m / 2

    sigma_y = params.sigma_y_initial_m + params.sigma_y_coefficient * np.maximum(along, 0.0)
    ventilation = speed * params.mixing_height_m

    kernel = (
        np.exp(-0.5 * (across / sigma_y) ** 2)
        / (np.sqrt(2 * np.pi) * sigma_y * ventilation)
        # Dry deposition as a loss along the trajectory: the fraction surviving after
        # travel time along/speed, losing depth v_d per second out of a layer H deep.
        * np.exp(-deposition_velocity_m_s * np.maximum(along, 0.0) / ventilation)
    )
    # Nothing travels upwind. Everything the model knows about lateral spread is in
    # sigma_y; there is no back-diffusion term at these scales.
    #
    # A micrometre, not zero: cos(270 deg) is 1.8e-16 rather than 0, so a cell exactly
    # crosswind of the source comes out a hair downwind and picks up a nonzero - if
    # vanishing - concentration. Harmless numerically, but it makes "the plume goes that
    # way" untestable and the map subtly asymmetric.
    return np.where(along > 1e-6, kernel, 0.0)


def concentration(
    emissions_g_s: np.ndarray,
    canopy: np.ndarray,
    params: AirParameters,
    *,
    wind_speed_ms: float,
    wind_direction_deg: float,
    resolution_m: float,
) -> np.ndarray:
    """Locally-generated PM2.5, µg m-3, from a per-cell emission field.

    Not the total concentration - see the module docstring. The regional background is
    absent by construction, so read differences, not levels.
    """
    # caveat: one deposition velocity for the whole tile, weighted by its mean canopy.
    # Making it vary per cell would mean integrating v_d along every source-receptor path,
    # which is a per-pair calculation and forfeits the single convolution the interactivity
    # claim rests on. The cost is that planting inside a small polygon credits the whole
    # tile with a slightly better sink rather than crediting the polygon - a real effect,
    # attributed too widely. Upgrade path if canopy interventions ever need local air
    # credit: split the kernel by canopy band and convolve per band.
    mean_canopy = float(np.nanmean(canopy)) if np.isfinite(canopy).any() else 0.0
    deposition = params.deposition_velocity_bare_m_s + mean_canopy * (
        params.deposition_velocity_canopy_m_s - params.deposition_velocity_bare_m_s
    )

    kernel = plume_kernel(
        params,
        wind_speed_ms=wind_speed_ms,
        wind_direction_deg=wind_direction_deg,
        deposition_velocity_m_s=deposition,
        resolution_m=resolution_m,
    )
    field = fftconvolve(np.nan_to_num(emissions_g_s, nan=0.0), kernel, mode="same")
    # FFT round-off leaves ~1e-20 negatives where the answer is exactly zero. They are
    # meaningless, and a negative concentration on a map is not.
    return np.asarray(np.maximum(field, 0.0) * G_PER_M3_TO_UG_PER_M3)


def _scalar(cube: xr.Dataset, name: str) -> float:
    """One meteorology value from a single-window slice. Must be a real number.

    The finiteness check is the important part, and it was added after watching what
    happens without it. A window whose Open-Meteo fetch failed carries NaN, NaN propagates
    into the bearing, every comparison against NaN is False, and the kernel comes out
    **entirely zero** - so the core answers "your low-emission zone changes nothing"
    instead of "this window has no wind". A confident zero is the worst possible failure
    for this core, because zero is also a legitimate answer.
    """
    if name not in cube:
        raise ValueError(f"cube has no variable {name!r}; the air core needs it")
    values = np.asarray(cube[name].values).reshape(-1)
    if values.size != 1:
        raise ValueError(
            f"{name} has {values.size} values - pass one window "
            f"(state.cube.select_window), not the whole cube"
        )
    value = float(values[0])
    if not np.isfinite(value):
        raise ValueError(
            f"{name} is {values[0]} for this window, so the plume has no direction or "
            "speed to travel at. Rebuild the window's meteorology - a zero delta here "
            "would look like a finding."
        )
    return value


def season_of(cube: xr.Dataset) -> str:
    """The window's season, as the cube itself records it."""
    if "season" not in cube.coords:
        raise ValueError("cube slice carries no season coordinate; cannot pick parameters")
    return str(np.asarray(cube["season"].values).reshape(-1)[0])


def simulate(
    cube: xr.Dataset, intervention: Intervention, model: AirParameters | None = None
) -> CoreResult:
    """Predict the PM2.5 change caused by `intervention`. Whole tile, µg m-3.

    Two levers, and they are not symmetric:

    - **Emissions removed** inside the mask. Local, large, and the honest version of "ban
      combustion vehicles" - though note that brake, tyre and road wear are roughly half of
      road PM2.5 and do not go away when the engine changes, only when the traffic does.
    - **Canopy added**, which raises dry deposition. Real but second-order, and attributed
      tile-wide rather than locally - see the `caveat:` note in `concentration`.

    `model` defaults to the parameters for the window's own season. Passing summer
    parameters to a winter window is a mistake worth ~3x, so the default is derived from
    the cube rather than from a constant.
    """
    if EMISSION_VARIABLE not in cube:
        raise ValueError(
            f"cube has no {EMISSION_VARIABLE!r}; build the emission inventory first "
            "(scripts/build_air_layers.py)"
        )

    emissions = np.asarray(cube[EMISSION_VARIABLE].values, dtype="float64")
    if not np.isfinite(emissions).any():
        raise ValueError(
            f"{EMISSION_VARIABLE} is entirely unpopulated in this cube - the OSM ingest "
            "never ran or failed. A cube that opens is not a cube that is complete."
        )
    if intervention.mask.shape != emissions.shape:
        raise ValueError(
            f"intervention mask {intervention.mask.shape} != cube grid {emissions.shape}"
        )

    params = model if model is not None else AirParameters.for_season(season_of(cube))
    resolution_m = float(abs(np.diff(np.asarray(cube["x"].values)[:2])[0]))

    # The kernel maps a *lower* row index to further north, which is true of the canonical
    # grid and is not a law of nature. On a y-ascending cube every plume would be mirrored
    # north-south: still smooth, still plausible, entirely wrong. Cheaper to check than to
    # notice on a map.
    y = np.asarray(cube["y"].values)
    if y.size > 1 and y[0] < y[-1]:
        raise ValueError(
            "cube y coordinates ascend; the air core assumes the canonical grid's "
            "descending y (row 0 is north). Reorder before simulating."
        )

    arrays = {name: np.asarray(cube[name].values) for name in ("ndvi", "landcover")}
    canopy = canopy_fraction(arrays)
    # Same capping as the thermal core: you cannot plant on water, and a cell already at
    # closed canopy has no headroom. Reused rather than reimplemented, so the two cores
    # can never disagree about what an intervention actually planted.
    scenario_canopy = np.clip(canopy + effective_fraction(arrays, intervention), 0.0, 1.0)

    scenario_emissions = np.where(
        intervention.mask, emissions * (1.0 - intervention.emission_fraction_removed), emissions
    )

    # The same wind on both sides. A low-emission zone does not change the synoptic
    # conditions, and letting it would attribute the weather to the intervention.
    wind = {
        "wind_speed_ms": _scalar(cube, "wind_speed_ms"),
        "wind_direction_deg": _scalar(cube, "wind_direction_deg"),
        "resolution_m": resolution_m,
    }
    baseline = concentration(emissions, canopy, params, **wind)
    scenario = concentration(scenario_emissions, scenario_canopy, params, **wind)

    delta = (scenario - baseline).astype("float32")
    return CoreResult(
        variable=RESULT_VARIABLE,
        units=RESULT_UNITS,
        delta=delta,
        stats=summarise_delta(delta, intervention.mask, spillover_cells=AIR_SPILLOVER_CELLS),
    )


# --------------------------------------------------------------------------------
# Validation: leave one station out
# --------------------------------------------------------------------------------


class StationScore(BaseModel):
    """One held-out monitor: what it measured, and what the others' fit predicted."""

    model_config = ConfigDict(frozen=True)

    station: str
    observed_ugm3: float
    predicted_ugm3: float

    @property
    def residual(self) -> float:
        return self.predicted_ugm3 - self.observed_ugm3


class ValidationReport(BaseModel):
    """Leave-one-station-out skill against observations.

    Scored against a **null model** - predicting every station from the mean of the others
    - because with a handful of monitors in one city, a model that merely reproduces the
    city mean can post a respectable error and look like it resolved something spatial.
    `beats_null` is the only claim here worth making.
    """

    model_config = ConfigDict(frozen=True)

    scores: list[StationScore]
    # Pooled fit of observed = scale x modelled + background. `scale` is the correction the
    # emission inventory needs: 3.0 means the inventory is a third of reality.
    scale: float
    background_ugm3: float
    mae: float
    null_mae: float

    @property
    def beats_null(self) -> bool:
        return self.mae < self.null_mae


def leave_one_station_out(
    modelled: np.ndarray, observed: np.ndarray, stations: list[str]
) -> ValidationReport:
    """Fit on every station but one, predict the one, repeat.

    The fit is affine - `observed = scale * modelled + background` - and both terms are
    load-bearing. The core produces only the tile's *own* contribution, so the intercept is
    the regional background it does not model, and the slope is the scale error in an
    inventory built from literature emission factors. Fitting without the intercept would
    force the background into the slope and report an inventory 10x too small.
    """
    x = np.asarray(modelled, dtype="float64").reshape(-1)
    y = np.asarray(observed, dtype="float64").reshape(-1)
    if x.shape != y.shape or x.size != len(stations):
        raise ValueError("modelled, observed and stations must be the same length")
    # Three points fit a slope, an intercept and nothing else; the fourth is what makes
    # holding one out a test rather than an interpolation.
    if x.size < 4:
        raise ValueError(
            f"leave-one-out needs at least 4 stations, got {x.size}. Report the fit on all "
            "of them and say it is not validation."
        )
    # A slope through points that share an x is arbitrary, and numpy fits one anyway with
    # only a RankWarning. That report would carry a confident scale factor invented from
    # nothing - which is how this core's emission inventory would get "calibrated" against
    # a set of monitors that all sit away from the modelled sources.
    if np.ptp(x) == 0:
        raise ValueError(
            "every station has the same modelled concentration, so there is no slope to "
            "fit. The monitors are all outside the modelled plumes, or the inventory is "
            "empty where they stand."
        )

    scores: list[StationScore] = []
    null_errors: list[float] = []
    for i in range(x.size):
        others = np.arange(x.size) != i
        slope, intercept = np.polyfit(x[others], y[others], 1)
        scores.append(
            StationScore(
                station=stations[i],
                observed_ugm3=float(y[i]),
                predicted_ugm3=float(slope * x[i] + intercept),
            )
        )
        null_errors.append(abs(float(y[others].mean()) - float(y[i])))

    pooled_slope, pooled_intercept = np.polyfit(x, y, 1)
    return ValidationReport(
        scores=scores,
        scale=float(pooled_slope),
        background_ugm3=float(pooled_intercept),
        mae=float(np.mean([abs(s.residual) for s in scores])),
        null_mae=float(np.mean(null_errors)),
    )
