"""Run one intervention through the thermal core.

The route's whole job is translation: GeoJSON becomes a grid mask, a window label becomes
a 2-D cube, and the core's arrays become base64. The physics happens in `cores/thermal`,
which this module calls exactly once and does not second-guess.
"""

from __future__ import annotations

from typing import Annotated

import numpy as np
from fastapi import APIRouter, Depends, HTTPException

from terrarium.api.deps import get_runtime
from terrarium.api.geometry import GeometryError, mask_from_geojson
from terrarium.api.routes.cube import build_layer
from terrarium.api.runtime import Runtime
from terrarium.api.schemas.simulate import (
    DeltaStatsResponse,
    PlausibilityContext,
    SimulateRequest,
    SimulateResponse,
)
from terrarium.cores.base import Intervention
from terrarium.cores.thermal.features import BASE_VARIABLES
from terrarium.cores.thermal.simulate import (
    effective_fraction,
    simulate,
    tree_built_contrast,
)
from terrarium.state.cube import select_window

router = APIRouter(tags=["simulate"])

# Below this the tree-vs-built contrast is too small to divide by: winter reads
# 0.31-0.80 degC, so a ratio there is a small number over a smaller one and swings on
# noise that means nothing physically. Report null instead of a confident-looking number.
MIN_CONTRAST_FOR_RATIO_C = 1.0


@router.post(
    "/simulate",
    response_model=SimulateResponse,
    summary="Modelled ΔLST for a tree-planting intervention",
)
async def run_simulation(
    request: SimulateRequest,
    runtime: Annotated[Runtime, Depends(get_runtime)],
) -> SimulateResponse:
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
        # 422: the request was well-formed JSON but the geometry cannot be simulated.
        raise HTTPException(status_code=422, detail=str(exc)) from None

    window = select_window(runtime.cube, label)
    intervention = Intervention(
        mask=mask, canopy_fraction_added=request.canopy_fraction_added
    )

    try:
        result = simulate(window, intervention, runtime.model)
    except ValueError as exc:
        # The core raises this for a tile with no tree-cover reference to plant toward -
        # a property of the cube, not of the request, but the caller still needs to know.
        raise HTTPException(status_code=422, detail=str(exc)) from None

    # Context for the delta. Recomputing the capped fraction is a few array ops and
    # cheaper than widening the frozen `CoreResult` contract, which is shared with the
    # product track and not ours alone to change.
    arrays = {name: np.asarray(window[name].values) for name in BASE_VARIABLES}
    fraction = effective_fraction(arrays, intervention)
    planted = fraction > 0
    mean_added = float(fraction[planted].mean()) if planted.any() else 0.0

    contrast = tree_built_contrast(window)
    linear = -mean_added * contrast
    ratio = (
        result.stats.mean_delta_inside / linear
        if contrast >= MIN_CONTRAST_FOR_RATIO_C and linear
        else None
    )

    stats = result.stats
    return SimulateResponse(
        variable=result.variable,
        units=result.units,
        window=label,
        season=str(np.asarray(window["season"].values).reshape(-1)[0]),
        stats=DeltaStatsResponse(
            n_cells_changed=stats.n_cells_changed,
            mean_delta_inside=stats.mean_delta_inside,
            mean_delta_spillover=stats.mean_delta_spillover,
            spillover_cells=stats.spillover_cells,
            min_delta=stats.min_delta,
            max_delta=stats.max_delta,
        ),
        context=PlausibilityContext(
            tree_built_contrast_c=contrast,
            mean_canopy_added=mean_added,
            linear_expectation_c=linear,
            ratio_to_linear=ratio,
        ),
        delta=build_layer(
            result.delta,
            variable=result.variable,
            description="Modelled change in mid-morning land surface temperature",
            units=result.units,
            window=label,
            grid=runtime.grid,
        ),
    )
