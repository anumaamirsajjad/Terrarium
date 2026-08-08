"""Numbers out of the cores, sentences a councillor can read.

Deterministic and offline. There is no model in this file, on purpose: the explainer's job
is to say what the arrays actually contain and to attach every caveat the project has
earned, and a generative model is worse at both — it will smooth a caveat into a hedge and
occasionally restate a figure it did not receive. Templates cannot hallucinate a number.

The rule the whole module exists to enforce: **every figure leaves here with the three
things that make it quotable** — its window, its ceiling, and the hindcast correction. Any
one of them missing turns a defensible model output into an overclaim.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Phase 7 hindcast: across nine summers of real greening on this tile the emulator implied
# about 2.5x the cooling that actually materialised (-1.18 degC modelled against -0.47 degC
# observed, over-predicting in 12 of 12 test configurations). Every modelled cooling in a
# brief is divided by this before it is offered as an expectation, and the undivided figure
# is still shown next to it — the correction is a stated adjustment, never a silent one.
HINDCAST_OVERPREDICTION = 2.5

# Below this the tile's tree-vs-built contrast is too small to divide by (winter reads
# 0.3-0.8 degC). Mirrors the API's own threshold rather than inventing a second one.
MIN_CONTRAST_FOR_RATIO_C = 1.0


class EquityInputs(BaseModel):
    """The equity numbers a brief can speak about, or nothing."""

    model_config = ConfigDict(frozen=True)

    top_three_share: float
    densest_decile_share: float
    concentrated: bool
    shares_reliable: bool
    uninhabited_fraction: float


class AirInputs(BaseModel):
    """The air numbers, present only when the plan removed emissions."""

    model_config = ConfigDict(frozen=True)

    mean_delta_inside: float
    mean_delta_spillover: float
    spillover_cells: int
    units: str
    mixing_height_m: float
    emission_fraction_removed: float


class BriefInputs(BaseModel):
    """Everything the brief is allowed to talk about.

    A flat model of scalars rather than the API's response objects, so this module never
    imports `api/` — Layer 3 talking to itself is fine, but the DSL is the part that has to
    stay reusable by a script, and a script has no `SimulateResponse`.
    """

    model_config = ConfigDict(frozen=True)

    plan_name: str
    window: str
    season: str
    area_km2: float
    tree_count: int = 0
    cost_total_usd: float = 0.0

    mean_delta_inside: float
    mean_delta_spillover: float
    spillover_cells: int
    min_delta: float
    tree_built_contrast_c: float
    mean_canopy_added: float
    ratio_to_linear: float | None = None

    equity: EquityInputs | None = None
    air: AirInputs | None = None
    # What the plan *asked* of the air core, whether or not it could answer. Carried
    # separately from `air` so the brief can tell "this plan does not touch traffic" from
    # "it does, and this cube has no emission inventory to answer with" — two situations
    # that both arrive as `air is None` and mean opposite things.
    emission_fraction_requested: float = 0.0
    plan_notes: tuple[str, ...] = ()


class Brief(BaseModel):
    """One headline, the findings behind it, and what none of it proves."""

    model_config = ConfigDict(frozen=True)

    headline: str
    findings: tuple[str, ...]
    uncertainties: tuple[str, ...] = Field(
        description=(
            "Never empty. A brief with no uncertainty section is the failure mode this "
            "whole layer exists to avoid."
        )
    )
    confidence: Literal["low", "moderate"] = Field(
        description=(
            "Never 'high'. The thermal core is hindcast-corrected and the air core is "
            "uncalibrated, so nothing this project produces has earned that word yet."
        )
    )
    expected_cooling_c: float = Field(
        description=(
            "Modelled mean cooling inside the polygon, divided by the hindcast factor. "
            "The number to quote; the modelled figure is the upper bound next to it."
        )
    )


def _c(value: float, digits: int = 2) -> str:
    """Signed degrees, with the sign that makes cooling read as cooling."""
    return f"{value:+.{digits}f}"


def brief_for(inputs: BriefInputs) -> Brief:
    """Write the brief. Pure: same inputs, same sentences, always."""
    expected = inputs.mean_delta_inside / HINDCAST_OVERPREDICTION
    findings: list[str] = []
    uncertainties: list[str] = []

    planted = inputs.mean_canopy_added > 0

    if planted:
        headline = (
            f"{inputs.plan_name} over {inputs.area_km2:.2f} km2 cools this tile's "
            f"mid-morning land surface by {abs(inputs.mean_delta_inside):.2f} degC on "
            f"average inside the polygon in {inputs.window} — closer to "
            f"{abs(expected):.2f} degC once the hindcast correction is applied."
        )
        # Who receives it belongs in the headline, not three paragraphs down. It is the
        # only figure in this brief with no correction and no season-specific caveat
        # attached, and the question a council actually has to answer.
        if inputs.equity is not None and inputs.equity.shares_reliable:
            headline += (
                f" The densest tenth of the population receives "
                f"{inputs.equity.densest_decile_share:.0%} of it."
            )
    elif inputs.air is not None:
        headline = (
            f"{inputs.plan_name} over {inputs.area_km2:.2f} km2 removes "
            f"{inputs.air.mean_delta_inside:+.2f} {inputs.air.units} of locally-generated "
            f"PM2.5 inside the polygon in {inputs.window}, and changes no temperature."
        )
    elif inputs.emission_fraction_requested > 0:
        # The plan is fine; the cube cannot answer it. Saying "nothing measurable" here
        # would report a missing layer as a modelled result.
        headline = (
            f"{inputs.plan_name} removes "
            f"{inputs.emission_fraction_requested:.0%} of vehicle emissions over "
            f"{inputs.area_km2:.2f} km2, but the cube being served carries no emission "
            "inventory, so the air effect could not be modelled. It changes no temperature."
        )
    else:
        headline = f"{inputs.plan_name} over {inputs.area_km2:.2f} km2 changes nothing measurable."

    # --- thermal ---
    if planted:
        findings.append(
            f"Canopy actually added: {inputs.mean_canopy_added:.0%} per planted cell, "
            f"after each cell was capped at the room it still had"
            + (f" — about {inputs.tree_count:,} trees." if inputs.tree_count else ".")
        )
        findings.append(
            f"The ceiling on this window is {inputs.tree_built_contrast_c:.2f} degC — the "
            "tile's own observed gap between built-up and tree-cover pixels. No planting "
            "here can beat that, whatever the literature says about other cities."
        )
        if inputs.spillover_cells:
            findings.append(
                f"Cooling reaches past the polygon: {_c(inputs.mean_delta_spillover)} degC "
                f"across {inputs.spillover_cells:,} cells in the 200 m ring outside. That "
                "is the 500 m neighbourhood terms doing real work, not leakage."
            )
        findings.append(
            f"Best single cell: {_c(inputs.min_delta)} degC. A mean over a polygon hides "
            "how uneven the planting's effect is."
        )
        if inputs.ratio_to_linear is not None:
            findings.append(
                f"Against a purely linear response the model returns "
                f"{inputs.ratio_to_linear:.2f}x — slightly conservative, which is the "
                "expected direction."
            )

    # --- equity ---
    # Gated on `planted`, not just on the equity block being present. A traffic-only plan
    # produces a temperature delta of exactly zero, which makes the person-degree shares
    # divide by nothing and come back "unreliable" — reporting that as a finding would
    # describe a distribution the plan never attempted.
    equity = inputs.equity if planted else None
    if equity is not None and equity.shares_reliable:
        verdict = "concentrated in a few places" if equity.concentrated else "reasonably spread"
        findings.append(
            f"Who gets it: the three best-served population deciles hold "
            f"{equity.top_three_share:.0%} of the person-degrees (an even split is 30 %), "
            f"so the benefit is {verdict}. The densest decile receives "
            f"{equity.densest_decile_share:.0%}."
        )
        if equity.uninhabited_fraction > 0.25:
            findings.append(
                f"{equity.uninhabited_fraction:.0%} of the cooling, measured as "
                "degree-cells, lands where nobody lives. Moving the polygon toward "
                "housing buys more people per degree without costing another tree."
            )
    elif equity is not None:
        findings.append(
            "The equity split is withheld: this plan cools and warms in near-equal "
            "measure, so the shares divide by a vanishing net benefit and explode. That is "
            "a statement about the plan, not about the population data."
        )

    # --- air ---
    air = inputs.air
    if air is not None:
        findings.append(
            f"Locally-generated PM2.5 falls by {air.mean_delta_inside:+.2f} {air.units} "
            f"inside the zone and {air.mean_delta_spillover:+.2f} across "
            f"{air.spillover_cells:,} cells around it — a plume is still measurable a "
            "kilometre downwind, unlike cooling, which stops at the polygon's edge."
        )
        findings.append(
            f"Mixing height this window: {air.mixing_height_m:.0f} m. The same removal "
            "buys several times more under the winter inversion than in summer, because "
            "the same emissions are trapped in a third of the depth."
        )

    if air is None and inputs.emission_fraction_requested > 0:
        findings.append(
            "No air result: this plan removes emissions, but the cube being served carries "
            "no OSM emission inventory, so the question could not be answered here. That "
            "is a missing layer, not a modelled finding of no effect."
        )

    findings.extend(inputs.plan_notes)

    if inputs.cost_total_usd > 0:
        findings.append(
            f"Indicative cost: {inputs.cost_total_usd:,.0f} USD, from literature unit "
            "costs. Good for ranking two plans against each other; not a budget."
        )

    # --- uncertainties. Never conditional on the result being flattering, but a caveat is
    # attached to a figure, not to a plan: a plan that quotes no temperature does not carry
    # the temperature caveats, because a caveat about a number nobody was given is noise,
    # and noise is how a real caveat stops being read ---
    if planted:
        uncertainties.append(
            f"Modelled, not measured. Hindcast against nine summers of real greening on "
            f"this tile, the emulator over-predicted cooling by about "
            f"{HINDCAST_OVERPREDICTION:.1f}x in 12 of 12 test configurations. The headline "
            f"already carries that correction; the raw model figure is "
            f"{_c(inputs.mean_delta_inside)} degC."
        )
        uncertainties.append(
            f"This is {inputs.window} ({inputs.season}). The same planting cools roughly "
            "four times more in summer than in winter, so the window is part of the answer."
        )
        uncertainties.append(
            "Mid-morning *land surface* temperature — Landsat's ~10:30 overpass, the "
            "radiating surface. It runs several degrees above air temperature and peaks "
            "after the overpass, so this is not what a thermometer in the shade would read."
        )
    else:
        uncertainties.append(
            f"This is {inputs.window} ({inputs.season}). Under the November-January "
            "inversion the same emissions produce 6-9x the concentration they do in "
            "summer, so the window is part of the answer rather than a detail."
        )
    if planted and inputs.tree_built_contrast_c < MIN_CONTRAST_FOR_RATIO_C:
        uncertainties.append(
            f"The tree-vs-built contrast in this window is only "
            f"{inputs.tree_built_contrast_c:.2f} degC, so ratios against it are a small "
            "number over a smaller one and are withheld rather than shown."
        )
    if air is not None:
        uncertainties.append(
            "The PM2.5 figure is a *local increment*, not a concentration a monitor reads: "
            "the inventory covers this tile's roads and nothing else, so the regional "
            "background that dominates Lahore's absolute PM2.5 is absent by construction. "
            "It cancels in a difference, which is why only the difference is quoted."
        )
        uncertainties.append(
            "The emission factors are literature values for a South Asian fleet, so the "
            "magnitude carries a scale error of unknown size. The comparison between two "
            "plans survives it; the absolute number does not."
        )
        # Validation is season-specific, so the caveat has to be. Winter was scored against
        # 53 OpenAQ monitors and beats a null model; summer beats one at no setting. Saying
        # "unvalidated" on a winter figure understates it, and saying "validated" on a
        # summer figure is simply false - so neither sentence can be the general one.
        if inputs.season == "winter":
            uncertainties.append(
                "The winter spatial pattern was scored against 53 OpenAQ monitors over "
                "2025-winter and beats a null model (mean absolute error 40.6 against "
                "51.0 for predicting every station from the mean of the others). That is "
                "evidence the inventory puts high concentrations in roughly the right "
                "places; it is not evidence about the absolute level."
            )
        else:
            uncertainties.append(
                "The summer spatial pattern beats no null model at any setting tested, so "
                "where the summer PM2.5 change falls across the tile is unvalidated. "
                "Summer's boundary layer is deep and well mixed by mid-morning, so local "
                "sources disperse before they make a pattern. Read the summer figure as a "
                "magnitude, not as a map."
            )
        # OSM carries no brick kilns for Lahore - 0 in the tile and 0 in the wider region,
        # checked rather than assumed - so a major winter source is absent from every
        # modelled field. A ceiling on the core, not a tuning problem.
        uncertainties.append(
            "The inventory is roads-only because OpenStreetMap records no brick kilns "
            "anywhere near Lahore. Kilns are a major winter source here, so a whole "
            "source category the monitors can see is missing from the model."
        )
    if equity is not None:
        uncertainties.append(
            "Equity is stratified by population density, not deprivation. No free, keyless "
            "deprivation layer exists for Lahore, and substituting built-up density would "
            "be a claim the data cannot support."
        )

    return Brief(
        headline=headline,
        findings=tuple(findings),
        uncertainties=tuple(uncertainties),
        # 'moderate' only where the physics has been checked against something real, and
        # never 'high'. A summer air figure drops to 'low' because its spatial pattern
        # beats no null model; a winter one no longer has to, since it does (D21).
        confidence=(
            "low"
            if not planted or (air is not None and inputs.season != "winter")
            else "moderate"
        ),
        expected_cooling_c=expected,
    )
