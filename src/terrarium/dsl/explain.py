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
    # Carried for the plain summary only. "Person-degrees concentrated in three deciles"
    # is the honest unit and unreadable; "how many people live here" is the one a reader
    # already owns, and it is the same population the deciles were cut from.
    population_covered: float = 0.0


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


Impact = Literal["large", "moderate", "small", "marginal", "unrated", "none"]

# The phrase that goes in the headline for each verdict. A word like "marginal" is precise
# and means nothing to the reader this block is for, so the verdict travels as a structured
# field for the interface and as a sentence for the person.
_VERDICT_PHRASE: dict[Impact, str] = {
    "large": "a big change for one intervention",
    "moderate": "a real but modest change",
    "small": "a small change",
    "marginal": "close to doing nothing at all",
}


def _impact(expected: float, contrast_c: float) -> Impact:
    """How much this plan actually moves the needle, on the tile's own scale.

    Judged against the observed tree-vs-built gap rather than an absolute number of
    degrees, because the same 0.1 degC is a rounding error under a 4 degC ceiling and a
    real result under a 0.5 degC one — an absolute threshold would be wrong in one of the
    two windows whatever value it took.

    The cut points are the one *labelling* judgement in this module. They are not measured
    and they are not the physics; they decide which word a reader sees for a share the
    tile itself supplied. Kept as literals in one function so that stays obvious.
    """
    if expected >= -0.005:
        return "none"
    if contrast_c < MIN_CONTRAST_FOR_RATIO_C:
        # Winter. The share would divide a small number by a smaller one — the same reason
        # the API withholds `ratio_to_linear` here — so this falls back to the absolute
        # figure and deliberately cannot reach "large": there is nothing large available in
        # a window whose ceiling is half a degree.
        return "small" if abs(expected) >= 0.10 else "marginal"
    share = abs(expected) / contrast_c
    if share >= 0.30:
        return "large"
    if share >= 0.15:
        return "moderate"
    if share >= 0.05:
        return "small"
    return "marginal"


class PlainSummary(BaseModel):
    """The same result with the jargon taken out, for the dashboard.

    The technical brief above is written for somebody who will be asked to defend the
    number. This is written for somebody deciding whether the plan is worth doing, so it
    trades precision for a unit a person holds in their head: degrees against *this
    tile's own* leafy-street-versus-bare-street gap, people rather than person-degrees,
    and money rounded to something sayable.

    **It carries the corrected figure, never the raw one.** `expected_cooling_c` is already
    divided by the hindcast factor, and the plain summary is the one place a reader will
    not see the two side by side — so quoting the upper bound here would be the single
    easiest way to overclaim in this project.

    `source` says who wrote the prose. `template` is the deterministic path and the
    default; anything else is the narrator in `dsl/llm.py` having reworded these same
    numbers, which is the only thing it is allowed to do.
    """

    model_config = ConfigDict(frozen=True)

    verdict: Impact = Field(
        description=(
            "How big this is, on the tile's own scale. Computed here and **never** "
            "rewritten by the narrator, which may only touch the prose — so a model "
            "cannot talk a marginal plan up. 'unrated' is a plan whose only effect is on "
            "air, where the magnitudes are uncalibrated and sizing them would be a claim "
            "the core cannot back."
        )
    )
    headline: str = Field(description="One sentence: what this plan buys, in plain words")
    points: tuple[str, ...] = Field(
        description="Three to six short plain-language facts. Never empty."
    )
    caveat: str = Field(
        description=(
            "The single most important limitation, in one sentence. Plain language does "
            "not mean no caveat — it means the caveat is readable."
        )
    )
    source: str = Field(
        default="template",
        description="'template', or the provider chain that reworded it",
    )


class Brief(BaseModel):
    """One headline, the findings behind it, and what none of it proves."""

    model_config = ConfigDict(frozen=True)

    headline: str
    plain: PlainSummary = Field(
        description=(
            "The dashboard's version. Same numbers, plain words. Present always, because "
            "it is generated from templates and never depends on a key."
        )
    )
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


def _people(count: float) -> str:
    """A population a person can picture. Never more precision than the source has."""
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f} million people"
    if count >= 1_000:
        return f"{count / 1_000:.0f},000 people"
    return f"{count:.0f} people"


def plain_summary(inputs: BriefInputs, expected: float) -> PlainSummary:
    """The dashboard's version of the brief: same numbers, words a non-expert reads.

    Pure and template-driven, exactly like `brief_for`, and for the same reason — this is
    the copy most people will actually read, so it is the worst possible place for a
    figure the model was not given. `dsl.llm.narrate` may reword what comes out of here,
    and is checked against these numbers before its version is accepted.

    The framing choice worth defending: cooling is expressed **as a share of this tile's
    own tree-vs-built gap** rather than in bare degrees. "0.16 degC" reads as nothing to a
    non-specialist; "a sixth of the way from a bare street to a leafy one, here" is the
    same fact in a unit they can judge, and it carries the ceiling with it for free.
    """
    cooled = expected < -0.005
    air = inputs.air
    points: list[str] = []

    if not cooled and air is None:
        # Two different nothings, and telling a reader the wrong one is worse than telling
        # them neither: a traffic plan against a cube with no emission inventory has not
        # been found to do nothing, it has not been answered.
        if inputs.emission_fraction_requested > 0:
            return PlainSummary(
                verdict="none",
                headline=(
                    "This plan is about traffic, and the map being served here has no "
                    "traffic emissions in it - so there is nothing to work the answer out "
                    "from."
                ),
                points=(
                    "This is a gap in the data, not a finding that the plan would achieve "
                    "nothing.",
                    "Planting trees in the same area can be modelled right now; a traffic "
                    "ban cannot, until the emissions layer is loaded.",
                ),
                caveat=(
                    "Nothing here has been measured or modelled, so no number on this "
                    "screen describes what a low-emission zone would do."
                ),
            )
        return PlainSummary(
            verdict="none",
            headline=(
                f"This plan does not change {inputs.window.split('-')[0]}'s ground "
                "temperature by a measurable amount."
            ),
            points=(
                "Nothing in the drawn area had room for meaningful planting - it is "
                "mostly already green, already built over, or too small to matter.",
                "Try a larger area, or one with more bare ground and fewer buildings.",
            ),
            caveat=(
                "This is a model of the ground surface, not the air, and not a promise "
                "about any single street."
            ),
        )

    # A plan that only removes emissions. It gets its own summary rather than falling into
    # the "nothing happened" block above, which is what it used to do - a low-emission zone
    # being told that nothing had room for planting is simply the wrong answer to the
    # question that was asked.
    if not cooled and air is not None:
        return PlainSummary(
            verdict="unrated",
            headline=(
                f"{inputs.plan_name} across {inputs.area_km2:.1f} km2 clears out about "
                f"{abs(air.mean_delta_inside):.1f} {air.units} of the fumes this area's "
                "own traffic puts into the air. It does not change the temperature - "
                "taking cars off a road does not shade it."
            ),
            points=(
                "That is only the share these roads add. Most of what makes Lahore's air "
                "bad blows in from outside this area and does not go away.",
                (
                    "Winter is when this matters. Cold air sits on the city like a lid, so "
                    "the same traffic makes several times the difference it does in "
                    "summer."
                    if inputs.season == "winter"
                    else "Summer air is deep and moves, so the same traffic ban buys much "
                    "less now than it would under the winter smog."
                ),
                "The cleaner air drifts about a kilometre past the edge of the zone, so "
                "neighbours benefit too - unlike shade, which stops where the trees stop.",
            ),
            caveat=(
                "How big this change is has not been checked against real measurements - "
                "the amounts each vehicle gives off are borrowed from studies elsewhere, "
                "not counted here. Use it to compare one plan against another, not as a "
                "figure to quote on its own."
            ),
        )

    verdict = _impact(expected, inputs.tree_built_contrast_c)

    # Share of the ceiling. Guarded because winter's contrast is a few tenths of a degree,
    # where this ratio divides a small number by a smaller one - the same reason the API
    # withholds `ratio_to_linear` there.
    if inputs.tree_built_contrast_c >= MIN_CONTRAST_FOR_RATIO_C:
        share = abs(expected) / inputs.tree_built_contrast_c
        headline = (
            f"{inputs.plan_name} across {inputs.area_km2:.1f} km2 would make the ground "
            f"about {abs(expected):.2f} degC cooler on a {inputs.season} morning. That is "
            f"{_VERDICT_PHRASE[verdict]}: roughly {share * 100:.0f}% of the way from a "
            "bare street to a leafy one in this city."
        )
    else:
        headline = (
            f"{inputs.plan_name} across {inputs.area_km2:.1f} km2 would make the ground "
            f"about {abs(expected):.2f} degC cooler on a {inputs.season} morning - "
            f"{_VERDICT_PHRASE[verdict]}, because in winter even a fully tree-lined street "
            "is barely cooler than a bare one here."
        )

    if inputs.tree_count:
        points.append(
            f"What it takes: about {inputs.tree_count:,} trees, planted and kept alive for "
            "roughly three years before they are doing this much."
        )

    # The single most useful thing to tell somebody reading an average: no street gets the
    # average. The best cell is corrected by the same factor as the headline, because the
    # two sit in the same sentence and mixing a raw figure with a corrected one is how the
    # correction stops meaning anything.
    best = abs(inputs.min_delta) / HINDCAST_OVERPREDICTION
    if best > abs(expected) * 1.2:
        points.append(
            f"No street gets the average. The best-placed spots cool by about "
            f"{best:.2f} degC, while places already shaded or fully built over barely move "
            "- so where you plant matters as much as how many."
        )

    equity = inputs.equity
    if equity is not None and equity.population_covered > 0:
        points.append(
            f"{_people(equity.population_covered)} live across this tile. "
            + (
                "The cooling is not shared evenly - a few neighbourhoods get most of it."
                if equity.concentrated and equity.shares_reliable
                else "The cooling is spread fairly evenly across them."
            )
        )
    if equity is not None and equity.uninhabited_fraction > 0.25:
        # Actionable, and the cheapest improvement available to a reader: it costs a redraw
        # rather than another tree.
        points.append(
            f"About {equity.uninhabited_fraction:.0%} of the cooling lands where nobody "
            "lives. Moving the same area over housing would help far more people for the "
            "same trees and the same money."
        )

    if air is not None:
        points.append(
            f"Traffic fumes inside the zone fall by about "
            f"{abs(air.mean_delta_inside):.1f} {air.units}, and that cleaner air drifts "
            "about a kilometre past the edge. It is only the share these roads add, "
            "though - the haze that blows in from outside does not go away."
        )

    points.append(
        f"This is a {inputs.season} figure"
        + (
            ", and summer is when it counts - the same trees do far less on a winter "
            "morning."
            if inputs.season == "summer"
            else ". The same trees do several times more on a summer morning, which is "
            "when the heat is actually a problem."
        )
    )

    return PlainSummary(
        verdict=verdict,
        headline=headline,
        points=tuple(points),
        caveat=(
            "This is a model, not a measurement. It was checked against nine years of "
            "real tree planting in this city, where it promised about two and a half "
            "times more cooling than actually happened - so the number above has already "
            "been scaled down to match reality. It describes the ground surface in the "
            "late morning, which is hotter than the air you would feel in the shade."
        ),
    )


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
        plain=plain_summary(inputs, expected),
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
