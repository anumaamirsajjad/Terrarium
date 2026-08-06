"""Citizen photos in, typed observations on the grid out.

These routes deliberately do **not** depend on the runtime. The grid comes from
`config.ACTIVE_TILE`, which is a constant, so a deployment whose cube failed to load can
still take reports — the same reasoning that keeps `/plan/presets` cube-free.

`POST /observations` is the one endpoint in this project that **requires** a key. Every
other path degrades: the planner falls back to a regex, the cores are offline arithmetic.
Nothing falls back for a photograph, so this answers 503 with the reason rather than
returning an empty observation that would read like "the model saw nothing".
"""

from __future__ import annotations

import base64
import binascii
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from terrarium.api.geometry import GeometryError, cell_from_lonlat
from terrarium.api.observations import ObservationStore, StoredObservation
from terrarium.api.routes.cube import build_layer
from terrarium.api.schemas.observations import (
    SUPPORTED_MIME_TYPES,
    ObservationLayerResponse,
    ObservationListResponse,
    ObservationRequest,
    ObservationResponse,
)
from terrarium.config import Settings, get_settings
from terrarium.dsl.llm import adapter_from_key
from terrarium.dsl.observe import ObservationError, observation_from_photo
from terrarium.state.grid import grid_for_tile

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/observations", tags=["observations"])

STORE_ATTR = "terrarium_observations"

NO_READER = (
    "no vision model configured. Set TERRARIUM_GEMINI_API_KEY (free, no card) to read "
    "photos. Unlike the planner, this path has no offline fallback — no rule parser can "
    "read a photograph, so it stops rather than inventing an observation."
)


def get_store(request: Request) -> ObservationStore:
    """The process-wide store, created on first use.

    Lazily rather than in the app factory so a store exists even in an app built for a
    test that never mentions observations, and so its absence can never 500 a GET.
    """
    store = getattr(request.app.state, STORE_ATTR, None)
    if store is None:
        store = ObservationStore()
        setattr(request.app.state, STORE_ATTR, store)
    return store


def _reader_name(settings: Settings) -> str:
    adapter = adapter_from_key(settings.gemini_api_key, model=settings.gemini_model)
    return adapter.name if adapter is not None else NO_READER


def _stored_response(stored: StoredObservation) -> ObservationResponse:
    # The store speaks dataclasses and HTTP speaks Pydantic; this is the whole translation.
    return ObservationResponse(
        id=stored.id,
        observation=stored.observation,
        lon=stored.lon,
        lat=stored.lat,
        row=stored.row,
        col=stored.col,
    )


@router.post(
    "",
    response_model=ObservationResponse,
    summary="Read a citizen photo into a typed observation on the grid",
)
async def submit_observation(
    body: ObservationRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    store: Annotated[ObservationStore, Depends(get_store)],
) -> ObservationResponse:
    if body.mime_type not in SUPPORTED_MIME_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"mime_type {body.mime_type!r} is not one of {list(SUPPORTED_MIME_TYPES)}",
        )

    try:
        # Validated here rather than trusted onward: a payload that is not base64 would
        # otherwise be discovered by the provider, and come back as an opaque 400 from
        # somebody else's API.
        base64.b64decode(body.image_base64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(
            status_code=422, detail="image_base64 is not valid base64"
        ) from None

    grid = grid_for_tile(settings.tile)
    try:
        row, col = cell_from_lonlat(body.lon, body.lat, grid)
    except GeometryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    adapter = adapter_from_key(settings.gemini_api_key, model=settings.gemini_model)
    if adapter is None:
        raise HTTPException(status_code=503, detail=NO_READER)

    try:
        observation = observation_from_photo(
            image_base64=body.image_base64, mime_type=body.mime_type, adapter=adapter
        )
    except ObservationError as exc:
        # 502: the request was fine and an upstream this route depends on did not deliver.
        # Distinguishable from the 503 above, which says the dependency was never wired up.
        logger.warning("photo could not be read: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from None

    return _stored_response(store.add(observation, lon=body.lon, lat=body.lat, row=row, col=col))


@router.get(
    "",
    response_model=ObservationListResponse,
    summary="Every observation reported since this process started",
)
async def list_observations(
    settings: Annotated[Settings, Depends(get_settings)],
    store: Annotated[ObservationStore, Depends(get_store)],
) -> ObservationListResponse:
    return ObservationListResponse(
        observations=tuple(_stored_response(item) for item in store.all()),
        reader=_reader_name(settings),
    )


@router.get(
    "/layer",
    response_model=ObservationLayerResponse,
    summary="The reports as a raster on the canonical grid",
)
async def observation_layer(
    settings: Annotated[Settings, Depends(get_settings)],
    store: Annotated[ObservationStore, Depends(get_store)],
) -> ObservationLayerResponse:
    grid = grid_for_tile(settings.tile)
    return ObservationLayerResponse(
        layer=build_layer(
            store.severity_raster(grid),
            variable="citizen_severity",
            description=(
                "Worst severity reported by a citizen photo in each cell. Reports, not "
                "measurements — read by a language model and never written into the cube."
            ),
            units="severity 1-5",
            window=None,
            grid=grid,
        ),
        count=len(store.all()),
    )
