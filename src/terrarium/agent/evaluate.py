"""Run the real cores on one proposal and reduce the result to an `Outcome`.

Shared by the deterministic control and by the graph's `run` node, which is the whole
reason it is its own module: the control has to be scored **the same way** as the agent or
"the agent beat greedy" means nothing. One function, called from both, is the cheapest way
to make that claim honest.

There is no model in this file and no HTTP. It reads a loaded runtime and calls three pure
cores — the same three `/simulate` calls, in the same order, with the same arguments. If
this ever disagrees with `/simulate`, the winning plan will not reproduce when the user
applies it, so the rule is: **call the cores, never re-derive what they compute.**
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import numpy as np
import xarray as xr

from terrarium.agent.state import Candidate, Outcome
from terrarium.api.candidates import region_mask
from terrarium.api.runtime import Runtime
from terrarium.cores.air import EMISSION_VARIABLE, AirParameters
from terrarium.cores.air import simulate as simulate_air
from terrarium.cores.base import Intervention
from terrarium.cores.equity import benefit_distribution
from terrarium.cores.thermal.simulate import simulate as simulate_thermal
from terrarium.dsl.explain import HINDCAST_OVERPREDICTION
from terrarium.dsl.validate import M2_PER_KM2, ResolvedPlan

logger = logging.getLogger(__name__)


def evaluate(
    *,
    runtime: Runtime,
    window: xr.Dataset,
    regions: Sequence[Candidate],
    resolved: ResolvedPlan,
) -> Outcome:
    """Simulate `resolved` over `regions` and reduce the answer to comparable scalars.

    Raises `ValueError` from the thermal core when the window has no tree-cover reference,
    which is a property of the cube rather than of the proposal — the caller turns it into
    a stopped search, not a refused attempt, because retrying cannot help.
    """
    mask = region_mask(regions, runtime.grid)
    intervention = Intervention(
        mask=mask,
        canopy_fraction_added=resolved.canopy_fraction_added,
        emission_fraction_removed=resolved.emission_fraction_removed,
    )

    result = simulate_thermal(window, intervention, runtime.model)
    mean_inside = result.stats.mean_delta_inside

    return Outcome(
        mean_delta_inside_c=mean_inside,
        # Stated positive, and corrected. Every other cooling figure the product publishes
        # carries the hindcast division; a search whose target check skipped it would be
        # the one number in the project quoting the model's upper bound as an expectation.
        expected_cooling_c=max(-mean_inside, 0.0) / HINDCAST_OVERPREDICTION,
        person_degrees=_person_degrees(result.delta, runtime),
        people_reached=_people_inside(mask, runtime),
        cost_usd=resolved.cost.total_usd,
        tree_count=resolved.tree_count,
        area_km2=resolved.area_km2,
        delta_pm25=_air_delta(window, intervention),
    )


def _person_degrees(delta: np.ndarray, runtime: Runtime) -> float:
    """Tile-wide person-degrees, from the equity core rather than from a fresh sum.

    Tile-wide and not region-wide on purpose: the 500 m neighbourhood features push real
    cooling past the polygon's edge, and a search that only counted residents inside the
    blocks would systematically prefer regions with nobody living next door.

    Zero when the cube carries no population, or when nothing on it is inhabited. Both are
    properties of the cube, so they are the same answer for every candidate and cost the
    ranking nothing — but `person_degrees` then measures nothing, which is why the route
    reports which metric it actually used.
    """
    if "population" not in runtime.cube:
        return 0.0
    try:
        return benefit_distribution(
            delta, np.asarray(runtime.cube["population"].values)
        ).total_person_degrees
    except ValueError:
        return 0.0


def _people_inside(mask: np.ndarray, runtime: Runtime) -> float:
    """Residents living in the proposed region. Summed — population is extensive."""
    if "population" not in runtime.cube:
        return 0.0
    population = np.nan_to_num(np.asarray(runtime.cube["population"].values, dtype="float64"))
    return float(population[mask].sum())


def _air_delta(window: xr.Dataset, intervention: Intervention) -> float | None:
    """Mean local PM2.5 change inside the region, or `None` when there is none to have.

    Two different nulls collapse into one, exactly as `/simulate` collapses them: a plan
    that removes no emissions has no air question, and a cube with no inventory cannot
    answer one. Neither is scored — the search's metrics are all thermal — so this is
    carried for the trace and the report and nothing else.
    """
    if intervention.emission_fraction_removed <= 0 or EMISSION_VARIABLE not in window:
        return None

    season = str(np.asarray(window["season"].values).reshape(-1)[0])
    try:
        result = simulate_air(window, intervention, AirParameters.for_season(season))
    except ValueError as exc:
        logger.warning("air core skipped during search: %s", exc)
        return None
    return result.stats.mean_delta_inside


def summarise(outcome: Outcome, *, score_value: float, units: str) -> str:
    """One line a person — or a report prompt — can read. Every figure came from a core."""
    parts = [
        f"{outcome.expected_cooling_c:.2f} degC expected cooling",
        f"{outcome.area_km2:.1f} km2",
        f"{outcome.tree_count:,} trees",
        f"${outcome.cost_usd:,.0f}",
        f"{outcome.people_reached:,.0f} residents in the region",
        f"score {score_value:,.2f} {units}",
    ]
    if outcome.delta_pm25 is not None:
        parts.insert(1, f"{outcome.delta_pm25:+.2f} ug/m3 local PM2.5")
    return ", ".join(parts)


def area_km2(regions: Sequence[Candidate]) -> float:
    """Total area of a region selection, in km2. One line, but used in three prompts."""
    return sum(region.area_m2 for region in regions) / M2_PER_KM2
