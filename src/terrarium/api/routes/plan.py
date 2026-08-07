"""Turn a sentence, a button, or a DSL object into a runnable `/simulate` body.

The route is translation and nothing else, like every route here. It resolves the window,
rasterises the polygon, measures what the tile can take, and hands both to `dsl.validate`.
The refusals are the validator's and the sentences are the DSL's; this file only decides
which HTTP status each one deserves.

`GET /plan/presets` deliberately takes no runtime dependency. A deployment whose cube failed
to load still has a costed intervention library to show, and 503-ing a static list because
a Zarr store is missing would be answering the wrong question.
"""

from __future__ import annotations

import logging
from typing import Annotated

import numpy as np
from fastapi import APIRouter, Depends, HTTPException

from terrarium.api.deps import get_runtime
from terrarium.api.geometry import GeometryError, mask_from_geojson
from terrarium.api.measure import measure_polygon
from terrarium.api.runtime import Runtime
from terrarium.api.schemas.plan import (
    PlanRequest,
    PlanResponse,
    PlanSource,
    PresetsResponse,
)
from terrarium.api.schemas.simulate import SimulateRequest
from terrarium.config import Settings, get_settings
from terrarium.dsl.library import PRESETS, preset
from terrarium.dsl.llm import resolve_adapter
from terrarium.dsl.planner import PlanParseError, plan_from_text
from terrarium.dsl.schema import Plan
from terrarium.dsl.validate import PlanError, resolve
from terrarium.state.cube import select_window

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/plan", tags=["plan"])


def _planner_name(settings: Settings) -> str:
    adapter = resolve_adapter(settings)
    return adapter.name if adapter is not None else "rules (no model configured)"


@router.get(
    "/presets",
    response_model=PresetsResponse,
    summary="The costed intervention library",
)
async def list_presets(
    settings: Annotated[Settings, Depends(get_settings)],
) -> PresetsResponse:
    return PresetsResponse(presets=PRESETS, planner=_planner_name(settings))


def _plan_and_source(
    request: PlanRequest, settings: Settings
) -> tuple[Plan, PlanSource, tuple[str, ...]]:
    """Resolve the request's one source into a `Plan`.

    The language model is reached only from here, only when a key is configured, and only
    for `text`. Whatever it returns is a `Plan` or it is nothing: `plan_from_text` falls
    back to the rule parser on any invalid output, so this function cannot return an
    unvalidated model response.
    """
    if request.plan is not None:
        return request.plan, "explicit", ()

    if request.preset is not None:
        try:
            chosen = preset(request.preset)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc.args[0])) from None
        return chosen.plan, "preset", ()

    assert request.text is not None  # the request validator guarantees one of the three
    adapter = resolve_adapter(settings)
    try:
        parsed = plan_from_text(request.text, adapter=adapter)
    except PlanParseError as exc:
        # 422, not 400: the request was well formed and the sentence was not understood.
        # The message lists the vocabulary that *is* understood, which is the only useful
        # thing to say to someone who just typed something into a box.
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return parsed.plan, parsed.source, parsed.warnings


def _resolve_window(runtime: Runtime, request: PlanRequest, plan: Plan) -> str:
    """Pick the window, most specific request first.

    A season without a year is what people actually say, and it has to mean something: the
    same restriction buys several times more under the winter inversion, so silently
    answering "in winter" with the summer default would be off by that factor with nothing
    on screen to explain it.
    """
    requested = request.window or plan.window
    if requested is not None:
        if requested not in runtime.windows:
            raise HTTPException(
                status_code=404,
                detail=f"no window {requested!r} in this cube; have {runtime.windows}",
            )
        return requested

    if plan.season is not None:
        matching = [label for label in runtime.windows if label.endswith(str(plan.season))]
        if not matching:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"this cube has no {plan.season} window; have {runtime.windows}. The "
                    "season matters too much to substitute the other one."
                ),
            )
        return matching[-1]

    return runtime.default_window()


@router.post(
    "",
    response_model=PlanResponse,
    summary="Validate and cost an intervention before running it",
)
async def build_plan(
    request: PlanRequest,
    runtime: Annotated[Runtime, Depends(get_runtime)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> PlanResponse:
    plan, source, warnings = _plan_and_source(request, settings)
    label = _resolve_window(runtime, request, plan)

    try:
        mask = mask_from_geojson(request.geometry, runtime.grid)
    except GeometryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    window = select_window(runtime.cube, label)
    try:
        measurement = measure_polygon(window, mask, runtime.grid)
        resolved = resolve(plan, measurement)
    except PlanError as exc:
        # The refusal this layer exists for. 422 with the arithmetic: "5,000 trees need
        # 0.125 km2 of crown but only 0.031 km2 is plantable" is a sentence the user can
        # act on, and it arrives before any core has run.
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except ValueError as exc:
        # A window with no tree-cover reference. A property of the cube, not the request,
        # but it stops a plan the same way.
        raise HTTPException(status_code=422, detail=str(exc)) from None

    return PlanResponse(
        plan=resolved.plan,
        source=source,
        window=label,
        season=str(np.asarray(window["season"].values).reshape(-1)[0]),
        cells=resolved.cells,
        area_km2=resolved.area_km2,
        canopy_fraction_added=resolved.canopy_fraction_added,
        emission_fraction_removed=resolved.emission_fraction_removed,
        tree_count=resolved.tree_count,
        max_trees=resolved.max_trees,
        canopy_utilisation=resolved.canopy_utilisation,
        cost=resolved.cost,
        notes=resolved.notes,
        warnings=warnings,
        basis=resolved.basis,
        simulate_request=SimulateRequest(
            geometry=request.geometry,
            canopy_fraction_added=resolved.canopy_fraction_added,
            emission_fraction_removed=resolved.emission_fraction_removed,
            window=label,
        ),
    )
