"""The deterministic control: greedy over the same candidates, scored the same way.

**This is not a fallback, it is the control.** Every agent result reports this score beside
its own. A search result with nothing to beat is a claim, and this project does not ship
claims — the hindcast reports its own 2.5x over-prediction and the air validation says
plainly that summer beats no null model, and an agent that could not be shown to beat
anything would be the one component exempt from that standard.

It is also what makes the agent **falsifiable**. If the model never beats greedy, that is a
finding worth knowing and worth publishing, and it is only knowable because this exists.

No model, no randomness, no state. Same candidates, same cores, same `objective.score`.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import xarray as xr

from terrarium.agent.evaluate import evaluate
from terrarium.agent.objective import better, score, units_for
from terrarium.agent.state import Attempt, Candidate, Objective
from terrarium.api.candidates import region_measurement
from terrarium.api.runtime import Runtime
from terrarium.dsl.schema import Plan, PlantTrees, RestrictVehicles
from terrarium.dsl.validate import PlanError, resolve

logger = logging.getLogger(__name__)

# How many of the top-ranked regions the control simulates. Three rather than one because
# the ranking is a proxy and a proxy that is never checked is a guess; three rather than
# ten because the control must not cost more than the search it is a control for.
BASELINE_SIMULATIONS = 3

# The canopy the control asks for. A *fraction*, deliberately, so the control can never be
# refused: a fraction is documented as a ceiling the core caps per cell, where a tree count
# is a quantity that has to physically fit. A control that could fail to produce a number
# would leave the agent with nothing to be compared against exactly when the tile is
# difficult — which is when the comparison matters most.
#
# 0.30 is between the "street trees" (0.15) and "dense canopy" (0.40) presets: an ambitious
# retrofit rather than a park, which is what the lattice's 2 km blocks mostly contain.
BASELINE_CANOPY_FRACTION = 0.30

# The control's traffic lever, for a `pm25_reduction` objective. 1.0 — a full restriction —
# matches the "ban combustion vehicles" preset in `dsl/library.py`, for the same reason
# `BASELINE_CANOPY_FRACTION` matches a preset: the control has to be a plan somebody could
# actually propose, not a synthetic fraction chosen to make the comparison easy.
BASELINE_EMISSION_FRACTION = 1.0


def rank(candidates: Sequence[Candidate]) -> list[Candidate]:
    """Best-looking first, by `Candidate.opportunity` — plantable canopy x residents.

    Ties break on `region_id` so the control is reproducible: two blocks with identical
    headroom and identical population are otherwise ordered by whatever `build_lattice`
    happened to emit, and a control that reorders between runs is not a control.
    """
    return sorted(candidates, key=lambda c: (-c.opportunity, c.region_id))


def rank_by_emission(candidates: Sequence[Candidate]) -> list[Candidate]:
    """Best-looking first for a `pm25_reduction` objective, by road emissions alone.

    `opportunity` (canopy x population) ranks a block by how much *cooling* it could be
    worth, which has no relationship to how much traffic PM2.5 it emits — the two rankings
    routinely disagree, and using the thermal one here would hand the air control a set of
    leafy, quiet blocks to restrict traffic on instead of the busy ones.
    """
    return sorted(candidates, key=lambda c: (-c.emission_g_s, c.region_id))


def greedy_best(
    *,
    runtime: Runtime,
    window: xr.Dataset,
    candidates: Sequence[Candidate],
    objective: Objective,
    simulations: int = BASELINE_SIMULATIONS,
) -> tuple[Attempt | None, int]:
    """Simulate the top-ranked regions and keep the best. Returns `(best, calls_used)`.

    `None` when nothing could be simulated at all — every top region is water, or the
    window has no tree-cover reference. The caller reports that rather than substituting a
    zero, because "the control scored nothing" and "the control scored zero" are different
    statements about the tile.
    """
    units = units_for(objective)
    best: Attempt | None = None
    used = 0

    air = objective.metric == "pm25_reduction"
    ordered = rank_by_emission(candidates) if air else rank(candidates)

    for step, candidate in enumerate(ordered[:simulations]):
        plan = (
            Plan(
                name=f"Greedy restriction in {candidate.region_id}",
                actions=(RestrictVehicles(emission_fraction_removed=BASELINE_EMISSION_FRACTION),),
            )
            if air
            else Plan(
                name=f"Greedy planting in {candidate.region_id}",
                actions=(PlantTrees(canopy_fraction_added=BASELINE_CANOPY_FRACTION),),
            )
        )
        try:
            resolved = resolve(plan, region_measurement([candidate]))
        except PlanError as exc:
            # A block with literally no headroom — all water, or already closed canopy.
            # Ranked first only if the whole tile is like that, but it costs nothing to
            # carry the refusal into the trace rather than silently skipping it.
            best = best or None
            logger.info("control skipped %s: %s", candidate.region_id, exc)
            continue

        outcome = evaluate(runtime=runtime, window=window, regions=[candidate], resolved=resolved)
        used += 1
        attempt = Attempt(
            step=step,
            region_ids=(candidate.region_id,),
            plan=plan,
            status="scored",
            proposer="greedy",
            score=score(objective, outcome),
            outcome=outcome,
            reason=(
                f"greedy control, ranked #{step + 1} by road emissions"
                if air
                else f"greedy control, ranked #{step + 1} by plantable canopy x residents"
            ),
        )
        if better(attempt, best):
            best = attempt

    if best is not None:
        logger.info("control best: %s at %.3f %s", best.region_ids, best.score or 0.0, units)
    return best, used
