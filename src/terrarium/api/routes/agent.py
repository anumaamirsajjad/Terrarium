"""`POST /agent/search` — the intervention search, streamed.

Thin, like every route here. It resolves the window, builds the lattice, hands both to
`agent.graph.run_search` and serialises the events. The search is the agent package's; the
physics is the cores'; this file decides HTTP status codes and the wire format.

**SSE rather than a single response**, because the search is tens of seconds and A4's
argument is not about taste: a 40-second spinner is a broken feature, and a 40-second
visible search — each region proposed, each refusal with its arithmetic — is the feature.
"""

from __future__ import annotations

import logging
import uuid
from collections import OrderedDict
from collections.abc import Iterator
from typing import Annotated

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from terrarium.agent.graph import run_search
from terrarium.agent.state import SearchResult
from terrarium.api.candidates import BLOCK_CELLS, build_lattice
from terrarium.api.deps import get_runtime, require_model
from terrarium.api.runtime import Runtime
from terrarium.api.schemas.agent import CandidatesResponse, SearchRequest, SearchResponse
from terrarium.config import Settings, get_settings
from terrarium.state.cube import select_window

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["agent"])

# Finished searches, so a reloaded page can read its result back. Bounded and in-process:
# scope says no persistence of user scenarios, and this is a stream buffer rather than
# storage — it does not survive a restart and nothing depends on it doing so.
_RECENT_LIMIT = 20
_recent: OrderedDict[str, SearchResult] = OrderedDict()


def _remember(result: SearchResult) -> None:
    _recent[result.search_id] = result
    while len(_recent) > _RECENT_LIMIT:
        _recent.popitem(last=False)


def _resolve_window(runtime: Runtime, requested: str | None) -> str:
    try:
        return runtime.resolve_window(requested)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"no window {requested!r} in this cube; have {runtime.windows}",
        ) from None


@router.get(
    "/candidates",
    response_model=CandidatesResponse,
    summary="The lattice of regions the agent searches over",
)
async def list_candidates(
    runtime: Annotated[Runtime, Depends(get_runtime)],
    window: Annotated[str | None, Query(description="Default: latest summer")] = None,
) -> CandidatesResponse:
    label = _resolve_window(runtime, window)
    try:
        candidates = build_lattice(select_window(runtime.cube, label), runtime.grid)
    except ValueError as exc:
        # No tree-cover reference in this window. A property of the cube, not the request,
        # but it stops a search the same way `/simulate` is stopped by it.
        raise HTTPException(status_code=422, detail=str(exc)) from None

    return CandidatesResponse(window=label, block_cells=BLOCK_CELLS, candidates=candidates)


@router.post(
    "/search",
    summary="Search the tile for the best intervention (server-sent events)",
    response_class=StreamingResponse,
)
async def search(
    request: SearchRequest,
    runtime: Annotated[Runtime, Depends(get_runtime)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> StreamingResponse:
    """Stream one search as `text/event-stream`, one event per node transition.

    The 422s are raised *before* the stream opens, so a client that cannot start gets a
    status code rather than an error event inside a 200. Once the stream is open every
    failure arrives as an `error` event, because a truncated event stream with no body is
    the one thing a browser cannot report usefully.
    """
    # Refused before the stream opens, so a client with no key gets a status code rather
    # than a 200 whose first event is an error. There is no deterministic search behind
    # this any more — see `agent.nodes.SearchUnavailable`. Called in the handler rather
    # than as a route dependency so body validation wins: a malformed request is 422 on
    # every deployment.
    require_model(settings)

    label = _resolve_window(runtime, request.window)
    window = select_window(runtime.cube, label)

    try:
        candidates = build_lattice(window, runtime.grid)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    season = str(np.asarray(window["season"].values).reshape(-1)[0])
    search_id = uuid.uuid4().hex[:12]

    def stream() -> Iterator[str]:
        for event in run_search(
            runtime=runtime,
            window=window,
            label=label,
            season=season,
            candidates=candidates,
            goal=request.goal,
            settings=settings,
            budget=request.budget,
            search_id=search_id,
        ):
            if event.result is not None:
                _remember(event.result)
            yield f"event: {event.node}\ndata: {event.model_dump_json()}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            # Without this an nginx or a CDN in front of the API buffers the whole stream
            # and delivers it at the end, which turns the live trace back into the
            # 40-second spinner this route exists to avoid.
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    "/search/{search_id}",
    response_model=SearchResponse,
    summary="Read back a finished search",
)
async def read_search(search_id: str) -> SearchResponse:
    result = _recent.get(search_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"no search {search_id!r}. Results are held in memory for the last "
                f"{_RECENT_LIMIT} searches and do not survive a restart."
            ),
        )
    return SearchResponse(result=result)
