"""Request and response contracts for `POST /simulate`.

This is the interface between the physics track and the product track (see the plan's
§Tracks), so its shape is a two-person decision rather than a refactor. It deliberately
mirrors `cores.base.CoreResult` without importing its numpy-carrying models onto the
wire: the core speaks arrays, HTTP speaks base64 and JSON.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from terrarium.api.schemas.cube import LayerResponse
from terrarium.dsl.explain import Brief


class SimulateRequest(BaseModel):
    """Plant trees inside a drawn polygon and ask what it does to the surface."""

    # `extra="forbid"` because the two fractions have defaults: a client that misspells
    # `emission_fraction_removed` otherwise gets 200 and a thermal-only answer, which is
    # indistinguishable from a plan that asked for no traffic change. Same drift class as
    # A18/A24 — reject it at the boundary rather than answering a different question.
    model_config = ConfigDict(frozen=True, extra="forbid")

    geometry: dict[str, Any] = Field(
        description=(
            "WGS84 GeoJSON Polygon or MultiPolygon — a bare geometry, a Feature, or a "
            "single-feature FeatureCollection. Converted to a grid mask server-side (D6)."
        )
    )
    canopy_fraction_added: float = Field(
        default=0.30,
        ge=0.0,
        le=1.0,
        description=(
            "Canopy cover to add, as a fraction of cell area. Capped per cell against "
            "what is actually still plantable there, so the requested figure is a "
            "ceiling and not a promise — the response reports what was really added."
        ),
    )
    emission_fraction_removed: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description=(
            "Share of road and kiln PM2.5 emissions removed inside the polygon — a "
            "low-emission zone. 0.0, the default, means the plan says nothing about "
            "traffic and no air result is returned. Note that brake, tyre and road wear "
            "are roughly half of road PM2.5 and do not fall when the engine changes, only "
            "when the traffic does: 1.0 means the vehicles are gone, not electrified."
        ),
    )
    window: str | None = Field(
        default=None,
        description=(
            "Seasonal window to simulate. Defaults to the latest summer. This matters: "
            "the same planting cools ~0.51 degC in summer and ~0.13 degC in winter, so "
            "the window is part of the answer and is echoed back. It matters more for "
            "air: the winter inversion concentrates identical emissions several-fold."
        ),
    )


class DeltaStatsResponse(BaseModel):
    """Scalar summary of the delta field — what a panel shows without the raster."""

    model_config = ConfigDict(frozen=True)

    n_cells_changed: int
    mean_delta_inside: float = Field(description="Mean ΔLST within the intervention, degC")
    mean_delta_spillover: float = Field(
        description=(
            "Mean ΔLST in the 200 m ring just outside. Real physics, not leakage: the "
            "500 m neighbourhood features carry cooling past the polygon's edge. "
            "Measured on the ring rather than the whole tile, because past the feature "
            "neighbourhood the delta is exactly zero and averaging over 40,000 untouched "
            "cells would report ~0.000 and read as 'no spillover'."
        )
    )
    spillover_cells: int
    min_delta: float = Field(description="Strongest cooling anywhere on the tile, degC")
    max_delta: float = Field(description="Largest warming anywhere on the tile, degC")


class PlausibilityContext(BaseModel):
    """The numbers that stop a ΔLST being quoted naked.

    A cooling figure on its own invites the reader to compare it to the literature's
    3-6 degC and conclude the model is broken. It is not: this tile's *observed*
    tree-vs-built contrast is only 2.6-2.8 degC in summer, because at 100 m Lahore's
    "tree cover" is scattered street trees rather than closed canopy. Shipping the
    ceiling alongside the delta is what makes the delta defensible.
    """

    model_config = ConfigDict(frozen=True)

    tree_built_contrast_c: float = Field(
        description=(
            "This window's own observed median LST gap, built-up minus tree cover. The "
            "ceiling: a full conversion to tree cover cannot buy more than this."
        )
    )
    mean_canopy_added: float = Field(
        description="Canopy fraction actually added per planted cell, after capping"
    )
    linear_expectation_c: float = Field(
        description="contrast x mean canopy added — what a purely linear response predicts"
    )
    ratio_to_linear: float | None = Field(
        description=(
            "Modelled / linear. Around 0.7 in summer — slightly conservative, as expected. "
            "Null when the contrast is ~0, which is winter, where this ratio divides a "
            "small number by a smaller one and swings on meaningless noise."
        )
    )


class DecileShareResponse(BaseModel):
    """One tenth of the tile's residents, and what the intervention did for them."""

    model_config = ConfigDict(frozen=True)

    decile: int = Field(description="1 = least densely populated, 10 = most")
    people: float
    mean_delta_c: float = Field(description="Population-weighted mean ΔLST for this group")
    share: float = Field(description="Share of total person-degrees. 0.10 is an even share")


class EquityResponse(BaseModel):
    """Who actually receives the cooling.

    Deciles hold a tenth of the *population* each, not a tenth of the pixels, so an even
    distribution is 0.10 everywhere and any skew is visible without arithmetic. The unit
    behind `share` is person-degrees: a degree delivered where 500 people live counts 500
    times a degree delivered where nobody does.
    """

    model_config = ConfigDict(frozen=True)

    deciles: list[DecileShareResponse]
    stratified_by: str = Field(
        description=(
            "What ordered the deciles. 'population density' today — no deprivation layer "
            "is free and keyless, and substituting built-up density for deprivation would "
            "be a claim the data cannot support."
        )
    )
    population_covered: float
    top_three_share: float = Field(
        description="Held by the three best-served deciles. Even sharing is 0.30"
    )
    concentrated: bool = Field(
        description=(
            "True when a majority of the benefit reaches three deciles out of ten. "
            "Always false when `shares_reliable` is false."
        )
    )
    shares_reliable: bool = Field(
        description=(
            "False when the plan cools and warms in near-equal measure. Shares divide by "
            "the net benefit, so a vanishing denominator makes them explode — a half-"
            "cooling, half-warming tile produces shares of ±2010%. Do not draw the "
            "distribution when this is false; say the plan has no net effect to share."
        )
    )
    net_to_gross: float = Field(
        description="Net benefit over total temperature movement. 1.0 is pure cooling"
    )
    uninhabited_fraction: float = Field(
        description=(
            "Share of the cooling, measured as degree-cells, landing where nobody lives. "
            "Not person-degrees: an empty cell scores zero of those by construction, so "
            "the question would answer itself."
        )
    )


class AirResponse(BaseModel):
    """The modelled change in locally-generated PM2.5.

    **A local increment, not what a monitor reads.** The emission inventory covers this
    tile's roads and kilns and nothing else, so the regional background — which dominates
    Lahore's absolute PM2.5 — is absent by construction. It cancels in a difference, which
    is why this block carries a delta and its statistics but never a concentration level.
    """

    model_config = ConfigDict(frozen=True)

    variable: str
    units: str
    stats: DeltaStatsResponse
    mixing_height_m: float = Field(
        description=(
            "Depth the plume is assumed mixed through. ~250 m under the winter inversion "
            "against ~800 m in summer, which is most of why the same emissions produce "
            "much worse winter air."
        )
    )
    wind_speed_ms: float
    wind_direction_deg: float = Field(
        description="Meteorological convention: the direction the wind comes *from*"
    )
    emission_fraction_removed: float
    delta: LayerResponse


class SimulateResponse(BaseModel):
    """The modelled effect of one intervention."""

    model_config = ConfigDict(frozen=True)

    variable: str
    units: str
    window: str = Field(description="The window actually simulated — never assume the default")
    season: str
    stats: DeltaStatsResponse
    context: PlausibilityContext
    equity: EquityResponse | None = Field(
        default=None,
        description=(
            "Null when the served cube carries no population layer. Absent rather than "
            "zeroed, so a client never reports a distribution that was never computed."
        ),
    )
    delta: LayerResponse = Field(
        description=(
            "Whole-tile ΔLST, scenario minus baseline — both predicted by the same model. "
            "Never prediction-minus-observation, which would leak the model's residual "
            "onto every untouched pixel."
        )
    )
    air: AirResponse | None = Field(
        default=None,
        description=(
            "Null unless the request removed emissions *and* the served cube carries an "
            "OSM emission inventory. Absent rather than zeroed: a client cannot tell a "
            "plan that does nothing for air from a cube that cannot answer the question."
        ),
    )
    brief: Brief = Field(
        description=(
            "The same numbers as sentences, written deterministically from the blocks "
            "above — no language model, so it cannot restate a figure it did not receive. "
            "Always present and its `uncertainties` list is never empty: the hindcast "
            "correction, the window, and the surface-vs-air distinction travel with every "
            "figure this API emits."
        )
    )
