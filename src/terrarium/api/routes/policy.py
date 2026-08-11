"""GET /policy/measures — what `scripts/extract_policy.py` has already extracted (Phase D).

Read-only, and takes no runtime dependency, for the same reason `GET /plan/presets` does
not: a deployment whose cube failed to load can still show what a published policy would
deliver. Unlike `/plan/presets` the list can be empty — nothing has been extracted until a
maintainer runs the script — and an empty list is the honest answer, not an error.

Phase D's extraction needs the Gemini key and has no offline fallback; this route needs
neither. It only reads back what that offline build step already wrote.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from terrarium.api.schemas.policy import PolicyMeasureItem, PolicyMeasuresResponse
from terrarium.config import Settings, get_settings
from terrarium.policy.store import read_measures
from terrarium.policy.to_plan import to_plan

router = APIRouter(prefix="/policy", tags=["policy"])


@router.get(
    "/measures",
    response_model=PolicyMeasuresResponse,
    summary="Policy measures already extracted, mapped onto the two levers",
)
async def list_measures(
    settings: Annotated[Settings, Depends(get_settings)],
) -> PolicyMeasuresResponse:
    items = tuple(
        PolicyMeasureItem(measure=measure, mapped=to_plan(measure))
        for measure in read_measures(settings.duckdb_path)
    )
    return PolicyMeasuresResponse(
        measures=items,
        expressible=sum(1 for item in items if item.mapped is not None),
    )
