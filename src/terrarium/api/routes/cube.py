"""Read the State Cube: what is in it, and one variable at a time as a raster.

Thin by construction. The cube is already loaded and already validated, so these routes
select, encode and return - no I/O, no computation beyond a min/max for the colour ramp.
"""

from __future__ import annotations

from typing import Annotated

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pyproj import Transformer

from terrarium.api.deps import get_runtime
from terrarium.api.runtime import Runtime, spatial_variable
from terrarium.api.schemas.cube import (
    CubeSummaryResponse,
    GridInfo,
    LayerResponse,
    encode_array,
    layer_window,
)
from terrarium.state.cube import (
    VARIABLES_BY_NAME,
    Dims,
    select_window,
    window_valid_fractions,
)
from terrarium.state.grid import Grid

router = APIRouter(prefix="/cube", tags=["cube"])

WGS84 = "EPSG:4326"


def grid_info(grid: Grid) -> GridInfo:
    """The grid's geometry, in the analysis CRS and in lon/lat for the map."""
    left, bottom, right, top = grid.bounds
    transformer = Transformer.from_crs(grid.crs, WGS84, always_xy=True)
    # All four corners, not two: a UTM rectangle reprojects to a slight quadrilateral,
    # so taking the envelope guarantees the reported box covers the whole raster.
    lons, lats = transformer.transform([left, right, right, left], [bottom, bottom, top, top])

    return GridInfo(
        crs=grid.crs,
        resolution_m=grid.resolution_m,
        shape=grid.shape,
        bounds=grid.bounds,
        bounds_wgs84=(min(lons), min(lats), max(lons), max(lats)),
    )


def build_layer(
    values: np.ndarray,
    *,
    variable: str,
    description: str,
    units: str,
    window: str | None,
    grid: Grid,
) -> LayerResponse:
    """Package a 2-D array as a wire-ready layer, with its range for the colour ramp."""
    array = np.asarray(values, dtype="float32")
    valid = np.isfinite(array)
    n_valid = int(valid.sum())

    return LayerResponse(
        variable=variable,
        description=description,
        units=units,
        window=window,
        grid=grid_info(grid),
        data=encode_array(array),
        vmin=float(array[valid].min()) if n_valid else None,
        vmax=float(array[valid].max()) if n_valid else None,
        valid_fraction=n_valid / array.size if array.size else 0.0,
    )


@router.get(
    "/summary",
    response_model=CubeSummaryResponse,
    summary="What is in the served cube",
)
async def cube_summary(
    runtime: Annotated[Runtime, Depends(get_runtime)],
) -> CubeSummaryResponse:
    summary = runtime.summary
    return CubeSummaryResponse(
        crs=summary.crs,
        resolution_m=summary.resolution_m,
        shape=summary.shape,
        windows=summary.windows,
        default_window=runtime.default_window(),
        variables=summary.variables,
        window_valid_fractions=window_valid_fractions(runtime.cube),
    )


@router.get(
    "/layer/{name}",
    response_model=LayerResponse,
    summary="One variable as a base64 float32 raster",
)
async def cube_layer(
    runtime: Annotated[Runtime, Depends(get_runtime)],
    name: Annotated[str, Path(description="Cube variable name, e.g. lst_c")],
    window: Annotated[
        str | None,
        Query(description="Seasonal window. Ignored for static variables. Default: latest summer"),
    ] = None,
) -> LayerResponse:
    try:
        spatial_variable(name)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"no variable {name!r}; have {sorted(VARIABLES_BY_NAME)}",
        ) from None
    except ValueError as exc:
        # 422, matching /simulate's treatment of a well-formed request that cannot be
        # carried out: the variable exists and the URL is fine, but it is not a map.
        raise HTTPException(status_code=422, detail=str(exc)) from None

    try:
        label = runtime.resolve_window(window)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"no window {window!r} in this cube; have {runtime.windows}",
        ) from None

    spec = VARIABLES_BY_NAME[name]
    # Static variables are identical in every window, so selecting one is harmless - but
    # the response reports `window: null` for them rather than implying a time variation.
    source = select_window(runtime.cube, label) if spec.dims is Dims.TIME_SPACE else runtime.cube

    return build_layer(
        np.asarray(source[name].values),
        variable=name,
        description=spec.description,
        units=spec.units,
        window=layer_window(spec.dims, label),
        grid=runtime.grid,
    )
