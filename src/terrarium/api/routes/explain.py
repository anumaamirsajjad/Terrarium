"""`POST /explain/spatial` — where the cooling landed, and what was there.

Takes the same body as `/simulate` and runs the same core, because the pattern being
explained has to be the pattern that was shown. Recomputing costs one simulation (~0.84 s
warm) and is the honest alternative to accepting a delta field from the client, which
would let the thing being described be something the server never produced.

Kept off `/simulate` deliberately: this is a tap-to-explain affordance, not part of every
run, and putting a second model call on the critical path of the product's main endpoint
would cost every user latency for a panel most of them never open.
"""

from __future__ import annotations

import logging
from typing import Annotated

import numpy as np
from fastapi import APIRouter, Depends, HTTPException

from terrarium.api.deps import get_runtime
from terrarium.api.explain_spatial import (
    SpatialExplanation,
    as_table,
    explain_pattern,
)
from terrarium.api.geometry import GeometryError, mask_from_geojson
from terrarium.api.runtime import Runtime
from terrarium.api.schemas.simulate import SimulateRequest
from terrarium.config import Settings, get_settings
from terrarium.cores.base import Intervention
from terrarium.cores.thermal.features import BASE_VARIABLES
from terrarium.cores.thermal.simulate import effective_fraction, simulate
from terrarium.dsl.llm import describe_pattern
from terrarium.state.cube import select_window

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/explain", tags=["explain"])


@router.post(
    "/spatial",
    response_model=SpatialExplanation,
    summary="Where the modelled cooling landed, and why there",
)
async def explain_spatial(
    request: SimulateRequest,
    runtime: Annotated[Runtime, Depends(get_runtime)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SpatialExplanation:
    try:
        label = runtime.resolve_window(request.window)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"no window {request.window!r} in this cube; have {runtime.windows}",
        ) from None

    try:
        mask = mask_from_geojson(request.geometry, runtime.grid)
    except GeometryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    window = select_window(runtime.cube, label)
    intervention = Intervention(
        mask=mask,
        canopy_fraction_added=request.canopy_fraction_added,
        emission_fraction_removed=request.emission_fraction_removed,
    )

    try:
        result = simulate(window, intervention, runtime.model)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    arrays = {name: np.asarray(window[name].values) for name in BASE_VARIABLES}
    regions = explain_pattern(
        window=window,
        label=label,
        grid=runtime.grid,
        delta=result.delta,
        canopy_added=effective_fraction(arrays, intervention),
        mask=mask,
    )

    if not regions:
        # Nothing measurably changed. The empty table is the honest answer and there is
        # nothing for a model to describe — asking it anyway is an invitation to describe
        # a pattern that is not there.
        return SpatialExplanation(window=label, regions=(), source="table")

    # The model sees this string and nothing else, and `_numbers_are_faithful` compares its
    # output against exactly this string.
    table = as_table(regions, label=label)
    described = describe_pattern(table, settings=settings)
    if described is None:
        return SpatialExplanation(window=label, regions=regions, source="table")

    summary, points, source = described
    return SpatialExplanation(
        window=label, regions=regions, summary=summary, points=points, source=source
    )
