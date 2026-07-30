"""Health check.

Doubles as the frontend's bootstrap call: it returns the active tile so the map knows
where to centre without duplicating the bbox in JavaScript.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from terrarium import __version__
from terrarium.api.schemas.health import HealthResponse, TileInfo
from terrarium.config import Settings, get_settings

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse, summary="Liveness + active tile")
async def health(settings: Annotated[Settings, Depends(get_settings)]) -> HealthResponse:
    tile = settings.tile
    return HealthResponse(
        service="terrarium",
        version=__version__,
        env=settings.env,
        tile=TileInfo(
            name=tile.name,
            country=tile.country,
            bbox=tile.bbox,
            centroid=tile.centroid,
            crs=tile.crs,
            target_resolution_m=tile.target_resolution_m,
        ),
    )
