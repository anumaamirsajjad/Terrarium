"""Response contracts for the health endpoint."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TileInfo(BaseModel):
    """The tile this deployment is configured for."""

    name: str
    country: str
    bbox: tuple[float, float, float, float] = Field(
        description="WGS84 [west, south, east, north]"
    )
    centroid: tuple[float, float] = Field(description="WGS84 [longitude, latitude]")
    crs: str
    target_resolution_m: int


class HealthResponse(BaseModel):
    """Liveness payload. Also tells the frontend where to point the map."""

    status: Literal["ok"] = "ok"
    service: str
    version: str
    env: str
    tile: TileInfo
