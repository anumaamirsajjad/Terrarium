"""The intervention library: presets a user can offer as a button.

A preset is a plan somebody could plausibly propose to a council. Deliberately shallow — it
carries no geometry, since the user draws that — which is what lets one preset apply to
whatever polygon is on screen.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from terrarium.config import Season
from terrarium.dsl.schema import TREE_CANOPY_M2, Plan, PlantTrees, RestrictVehicles


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
            "Exists to make the season visible: identical emissions produce 6-9x the "
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
    a fraction still has a headline number to quote: a person can picture 4,000 trees in a
    way "15% canopy" does not conjure on its own.
    """
    return round(canopy_fraction_added * area_m2 / TREE_CANOPY_M2)
