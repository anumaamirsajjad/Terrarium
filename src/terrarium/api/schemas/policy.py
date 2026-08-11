"""Response contract for `GET /policy/measures` (Phase D).

`PolicyMeasure` and `MappedPlan` cross the wire unchanged, for the same reason `PlanResponse`
carries `dsl.schema.Plan` unchanged (see `api/schemas/plan.py`): they are already frozen
Pydantic models in the layer below, and a mirrored schema here would only be a second copy
to keep in step with the first.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from terrarium.policy.schema import PolicyMeasure
from terrarium.policy.to_plan import MappedPlan


class PolicyMeasureItem(BaseModel):
    """One extracted measure, and what it became — or `None` when neither lever can say it.

    `mapped` is recomputed from `measure` on every request rather than trusted from the
    DuckDB row it was read from (`policy.to_plan.to_plan` is pure and cheap), so this route
    can never disagree with the module that owns the mapping.
    """

    model_config = ConfigDict(frozen=True)

    measure: PolicyMeasure
    mapped: MappedPlan | None = Field(
        description="None means this measure names no lever this tile has — see `measure`"
    )


class PolicyMeasuresResponse(BaseModel):
    """What `scripts/extract_policy.py` has extracted so far. `()` before it has run.

    No model is reached to serve this — it is a read of the DuckDB catalogue the build step
    already wrote, which is what lets this route answer with no key (D27's build-step
    exception, not its route exception).
    """

    model_config = ConfigDict(frozen=True)

    measures: tuple[PolicyMeasureItem, ...]
    expressible: int = Field(description="How many of `measures` carry a `mapped` plan")
