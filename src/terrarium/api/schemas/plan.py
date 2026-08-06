"""Request and response contracts for `POST /plan` and `GET /plan/presets`.

`/plan` does not simulate. It answers one question — *can this polygon hold this plan, and
what would it cost?* — and hands back the exact `/simulate` body to post next. Keeping the
two apart is what makes the DSL visible: a refusal arrives before any physics runs, with the
arithmetic that produced it, rather than as a small delta the user has to interpret.

The DSL's own models (`Plan`, `CostEstimate`) cross the wire unchanged. They are frozen
Pydantic models in the same layer, so mirroring them into a second set of near-identical
schemas would create a seam that exists only to drift.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from terrarium.api.schemas.simulate import SimulateRequest
from terrarium.dsl.library import CostEstimate, Preset
from terrarium.dsl.schema import Plan

PlanSource = Literal["llm", "rules", "preset", "explicit"]


class PlanRequest(BaseModel):
    """Where the plan comes from, and the polygon it applies to.

    Exactly one of `text`, `preset` and `plan`. They are three front doors onto the same
    DSL — a sentence, a button, and the object itself — and accepting two at once would
    mean guessing which the user meant.
    """

    model_config = ConfigDict(frozen=True)

    geometry: dict[str, Any] = Field(
        description=(
            "WGS84 GeoJSON Polygon or MultiPolygon, as `/simulate` takes. Required even "
            "for a preset: whether 5,000 trees fit is a question about the polygon, and "
            "there is no answer without one."
        )
    )
    text: str | None = Field(
        default=None,
        max_length=500,
        description=(
            "Plain language, e.g. 'plant 5,000 trees and ban cars here in winter'. Parsed "
            "by a language model when one is configured, and by a deterministic rule "
            "parser otherwise — the response says which."
        ),
    )
    preset: str | None = Field(
        default=None, description="Slug from `GET /plan/presets`"
    )
    plan: Plan | None = Field(
        default=None, description="A DSL plan built client-side. Validated like any other."
    )
    window: str | None = Field(
        default=None,
        description=(
            "Overrides whatever the plan says. Resolution order: this, then the plan's "
            "window, then its season against the cube's windows, then the default — the "
            "latest *summer*."
        ),
    )

    @model_validator(mode="after")
    def _exactly_one_source(self) -> PlanRequest:
        given = [self.text is not None, self.preset is not None, self.plan is not None]
        if sum(given) != 1:
            raise ValueError("give exactly one of text, preset or plan")
        return self


class PlanResponse(BaseModel):
    """A plan checked against a polygon: what it becomes, what it costs, what to run."""

    model_config = ConfigDict(frozen=True)

    plan: Plan
    source: PlanSource = Field(
        description=(
            "How the plan was produced. 'rules' is not a degraded answer — it is the "
            "deterministic parser, and it is what a deployment with no model key uses."
        )
    )
    window: str = Field(description="The window resolved for this plan. Never null")
    season: str

    cells: int
    area_km2: float
    canopy_fraction_added: float = Field(
        description="What `/simulate` will be sent. 0.0 means the plan does not plant"
    )
    emission_fraction_removed: float = Field(
        description="0.0 means the plan says nothing about traffic, not that it was tried"
    )
    tree_count: int = Field(
        description="Equivalent trees, at one crown each — see `basis` for the crown area"
    )
    max_trees: int = Field(
        description=(
            "How many trees this polygon could hold at all, measured from the cube's own "
            "canopy headroom rather than assumed from a density figure."
        )
    )
    canopy_utilisation: float = Field(
        description=(
            "Requested canopy over available canopy. Above 1.0 the core caps per cell and "
            "delivers less than asked; a tree count that would exceed it is refused outright."
        )
    )
    cost: CostEstimate
    notes: tuple[str, ...] = Field(
        description="Non-fatal warnings from validation. The plan still runs; read them"
    )
    warnings: tuple[str, ...] = Field(
        default=(), description="Where the parser had to assume, or overrule a model"
    )
    basis: str
    simulate_request: SimulateRequest = Field(
        description=(
            "Ready to POST to /simulate. The DSL's whole output is these three numbers "
            "plus the geometry — everything else here is explanation."
        )
    )


class PresetsResponse(BaseModel):
    """The costed intervention library.

    The fallback path the roadmap names, and a working product on its own: these buttons
    emit exactly the DSL a language model would, so the demo needs no key and no network.
    """

    model_config = ConfigDict(frozen=True)

    presets: tuple[Preset, ...]
    planner: str = Field(
        description=(
            "What `/plan` will parse free text with: a provider and model, or "
            "'rules (no model configured)'."
        )
    )
