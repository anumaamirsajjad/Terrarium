"""The costed intervention library: presets, and what they cost.

Two things live here because they are the same claim from two directions. A preset is a
plan somebody could plausibly propose to a council; a cost is what makes it a proposal
rather than a wish. Both are deliberately shallow — a preset carries no geometry (the user
draws that) and a cost carries no local procurement data (nobody has any), and both say so.

**The cost figures are order-of-magnitude literature values, not quotes for Lahore.** They
are the same category of number as the air core's emission factors: useful for comparing
two plans against each other, not for a budget line. Every estimate carries its `basis`
string for exactly that reason, and `calibrated` is `False` everywhere until somebody
prices a real planting contract.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from terrarium.config import Season
from terrarium.dsl.schema import TREE_CANOPY_M2, Plan, PlantTrees, RestrictVehicles

# Supply, plant, stake and water one street tree through establishment (~3 years). Municipal
# street-tree programmes in South Asia report figures around this once establishment is
# included; the number is dominated by the watering, not the sapling.
COST_PER_TREE_USD = 15.0

# First-year cost of a low-emission zone per km2: signage, ANPR cameras at the cordon, and
# enforcement. Scales with the *perimeter* in reality, not the area, which is why this is
# an order-of-magnitude figure and labelled as one — a long thin zone costs far more per
# km2 than a compact one, and the DSL has no perimeter to reason with.
COST_PER_KM2_RESTRICTION_USD = 250_000.0

CAPITAL_BASIS = (
    f"{COST_PER_TREE_USD:.0f} USD per tree (supply, planting and ~3 years of "
    f"establishment watering) and {COST_PER_KM2_RESTRICTION_USD:,.0f} USD per km2 of "
    "restriction (signage, cameras, first-year enforcement). Literature order-of-"
    "magnitude figures for comparing plans, not a quote for Lahore."
)


class CostEstimate(BaseModel):
    """What a plan would cost, and how much that number is worth."""

    model_config = ConfigDict(frozen=True)

    planting_usd: float = Field(ge=0.0)
    restriction_usd: float = Field(ge=0.0)
    total_usd: float = Field(ge=0.0)
    basis: str = Field(description="Where these unit costs come from, in one sentence")
    calibrated: bool = Field(
        default=False,
        description=(
            "False everywhere today. No Lahore procurement figure has been used, so treat "
            "the total as a scale, not a budget."
        ),
    )


def estimate_cost(*, tree_count: int, restricted_area_km2: float) -> CostEstimate:
    """Price a resolved plan. Pure arithmetic over two literature constants."""
    planting = tree_count * COST_PER_TREE_USD
    restriction = restricted_area_km2 * COST_PER_KM2_RESTRICTION_USD
    return CostEstimate(
        planting_usd=planting,
        restriction_usd=restriction,
        total_usd=planting + restriction,
        basis=CAPITAL_BASIS,
    )


class Preset(BaseModel):
    """A named plan the UI can offer as a button, plus the sentence that explains it.

    The plan holds no geometry, so a preset is applicable to whatever the user has drawn.
    This is the **fallback path** the roadmap names: preset buttons emit exactly the DSL an
    LLM would emit, so the product works with no model, no key and no network.
    """

    model_config = ConfigDict(frozen=True)

    slug: str
    label: str
    summary: str = Field(description="What this does, in one line, for a button subtitle")
    caveat: str = Field(description="What it does *not* do. Shown next to the result")
    plan: Plan


PRESETS: tuple[Preset, ...] = (
    Preset(
        slug="street-trees",
        label="Street trees",
        summary="Add 15 % canopy — roughly a row of trees down each street.",
        caveat=(
            "Realistic for a retrofit: most of a built-up cell is roof and carriageway, "
            "and the core caps every cell at the canopy it can still take."
        ),
        plan=Plan(
            name="Street trees",
            actions=(PlantTrees(canopy_fraction_added=0.15),),
        ),
    ),
    Preset(
        slug="dense-canopy",
        label="Dense canopy",
        summary="Add 40 % canopy — a park-grade planting, not a street retrofit.",
        caveat=(
            "Ambitious on purpose. Watch the capped figure in the result: on built-up "
            "cells the core will deliver well under 40 %, because the headroom is not there."
        ),
        plan=Plan(
            name="Dense canopy",
            actions=(PlantTrees(canopy_fraction_added=0.40),),
        ),
    ),
    Preset(
        slug="low-emission-zone",
        label="Low-emission zone",
        summary="Remove all vehicle PM2.5 inside the polygon. No planting.",
        caveat=(
            "1.0 means the traffic is gone, not electrified — brake, tyre and road wear "
            "are about half of road PM2.5 and survive an EV. Returns no temperature "
            "change, because the thermal emulator was never trained on traffic."
        ),
        plan=Plan(
            name="Low-emission zone",
            actions=(RestrictVehicles(emission_fraction_removed=1.0),),
        ),
    ),
    Preset(
        slug="clean-and-green",
        label="Clean and green",
        summary="25 % canopy and half the vehicle emissions removed.",
        caveat=(
            "The only preset that moves both cores. The two effects are independent here: "
            "planting does essentially nothing for PM2.5 at this scale (-0.0003 µg/m3 in "
            "Phase 9), so read the two numbers separately rather than as one policy."
        ),
        plan=Plan(
            name="Clean and green",
            actions=(
                PlantTrees(canopy_fraction_added=0.25),
                RestrictVehicles(emission_fraction_removed=0.5),
            ),
        ),
    ),
    Preset(
        slug="winter-inversion",
        label="Winter low-emission zone",
        summary="The same restriction, scored against the winter inversion.",
        caveat=(
            "Exists to make the season visible: identical emissions produce 6-7x the "
            "concentration under the November-January inversion, so the same plan buys "
            "several times more in winter than in summer."
        ),
        plan=Plan(
            name="Winter low-emission zone",
            actions=(RestrictVehicles(emission_fraction_removed=1.0),),
            season=Season.WINTER,
        ),
    ),
)

PRESETS_BY_SLUG: dict[str, Preset] = {preset.slug: preset for preset in PRESETS}


def preset(slug: str) -> Preset:
    """Look up one preset, or raise `KeyError` naming what does exist."""
    try:
        return PRESETS_BY_SLUG[slug]
    except KeyError:
        raise KeyError(
            f"no preset {slug!r}; have {sorted(PRESETS_BY_SLUG)}"
        ) from None


def trees_for_canopy(canopy_fraction_added: float, area_m2: float) -> int:
    """Equivalent tree count for a canopy fraction over an area.

    The inverse of the conversion in `dsl.validate`, and the reason a preset expressed as
    a fraction can still be costed: a cost needs a count, and a person needs a count to
    argue with.
    """
    return round(canopy_fraction_added * area_m2 / TREE_CANOPY_M2)
